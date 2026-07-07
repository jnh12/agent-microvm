"""Tests for the VPC egress network connector (config, resources, provisioner)."""

from __future__ import annotations

import pytest
from agent_sandbox.infra import resources as R
from agent_sandbox.infra.config import InfraConfig
from agent_sandbox.infra.provisioner import Provisioner
from agent_sandbox.infra.state import InfraStateStore
from botocore.exceptions import ClientError

# -- config parsing ---------------------------------------------------------


def test_egress_config_defaults_when_absent():
    cfg = InfraConfig.from_dict({"project": "p", "stack": "s", "region": "us-east-1"})
    assert cfg.network.enabled is False
    assert cfg.network.egress.reuse is False
    assert cfg.network.egress.name == "agent-sandbox-egress"


def test_egress_config_reuse():
    arn = "arn:aws:lambda:us-east-1:1:network-connector:nc"
    cfg = InfraConfig.from_dict({"network": {"egress": {"connector_arn": arn}}})
    assert cfg.network.enabled is True
    assert cfg.network.egress.reuse is True
    assert cfg.network.egress.connector_arn.endswith(":nc")


def test_egress_config_create_fields():
    cfg = InfraConfig.from_dict(
        {
            "network": {
                "egress": {
                    "name": "my-egress",
                    "vpc_id": "vpc-1",
                    "subnet_ids": ["subnet-a", "subnet-b"],
                    "security_group_id": "sg-1",
                    "operator_role_arn": "arn:role",
                }
            }
        }
    )
    e = cfg.network.egress
    assert cfg.network.enabled is True
    assert e.reuse is False
    assert e.reuse_sg is True
    assert e.name == "my-egress"
    assert e.vpc_id == "vpc-1"
    assert e.subnet_ids == ["subnet-a", "subnet-b"]
    assert e.operator_role_arn == "arn:role"


# -- resources: network connector -------------------------------------------


class FakeCore:
    def __init__(self, existing=None, states=("ACTIVE",)):
        self.existing = existing or []
        self.states = list(states)
        self.created = None
        self.deleted = None

    def list_network_connectors(self, **kw):
        return {"NetworkConnectors": self.existing}

    def create_network_connector(self, **kw):
        self.created = kw
        return {
            "Arn": "arn:aws:lambda:us-east-1:1:network-connector:nc-new",
            "Id": "nc-new",
            "State": "PENDING",
        }

    def get_network_connector(self, **kw):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"Arn": kw["Identifier"], "State": state, "StateReason": "boom"}

    def delete_network_connector(self, **kw):
        self.deleted = kw["Identifier"]


def test_ensure_network_connector_creates():
    core = FakeCore()
    out = R.ensure_network_connector(
        core, name="e", subnet_ids=["subnet-a"], security_group_ids=["sg-1"],
        operator_role_arn="arn:role",
    )
    assert out["arn"].endswith(":nc-new")
    assert core.created["Name"] == "e"
    assert core.created["OperatorRole"] == "arn:role"
    cfgblock = core.created["Configuration"]["VpcEgressConfiguration"]
    assert cfgblock["SubnetIds"] == ["subnet-a"]
    assert cfgblock["SecurityGroupIds"] == ["sg-1"]
    assert cfgblock["AssociatedComputeResourceTypes"] == ["MicroVm"]


def test_ensure_network_connector_adopts_existing():
    core = FakeCore(
        existing=[{"Name": "e", "Arn": "arn:existing", "State": "ACTIVE"}]
    )
    out = R.ensure_network_connector(
        core, name="e", subnet_ids=["subnet-a"], security_group_ids=["sg-1"],
        operator_role_arn="arn:role",
    )
    assert out["arn"] == "arn:existing"
    assert core.created is None  # adopted, not created


def _assume_error():
    return ClientError(
        {
            "Error": {
                "Code": "InvalidParameterValueException",
                "Message": "The service is unable to assume the provided "
                "NetworkConnectorOperatorRole. Please verify the trust policy.",
            }
        },
        "CreateNetworkConnector",
    )


def test_is_assume_role_error():
    assert R._is_assume_role_error(_assume_error()) is True
    other = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad subnet"}},
        "CreateNetworkConnector",
    )
    assert R._is_assume_role_error(other) is False


def test_ensure_network_connector_retries_assume_propagation(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)  # no real waiting
    calls = {"n": 0}

    class Flaky(FakeCore):
        def create_network_connector(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _assume_error()
            return super().create_network_connector(**kw)

    out = R.ensure_network_connector(
        Flaky(), name="e", subnet_ids=["subnet-a"], security_group_ids=["sg-1"],
        operator_role_arn="arn:role",
    )
    assert out["arn"].endswith(":nc-new")
    assert calls["n"] == 2  # retried once, then succeeded


def test_ensure_network_connector_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)

    class AlwaysAssumeError(FakeCore):
        def create_network_connector(self, **kw):
            raise _assume_error()

    with pytest.raises(ClientError):
        R.ensure_network_connector(
            AlwaysAssumeError(), name="e", subnet_ids=["subnet-a"],
            security_group_ids=["sg-1"], operator_role_arn="arn:role",
            assume_retry_timeout=0.0,
        )


def test_wait_network_connector_active():
    core = FakeCore(states=("PENDING", "ACTIVE"))
    # No real sleeping thanks to a tiny timeout and immediate ACTIVE on 2nd poll.
    assert R.wait_network_connector_active(core, "arn:x", timeout=30) == "ACTIVE"


def test_wait_network_connector_failed_raises():
    core = FakeCore(states=("FAILED",))
    with pytest.raises(RuntimeError):
        R.wait_network_connector_active(core, "arn:x", timeout=30)


def test_resolve_subnet_ids_maps_names_and_passes_ids():
    ec2 = FakeEc2()
    out = R.resolve_subnet_ids(ec2, ["subnet-abc", "golden-subnet-01"], "vpc-1")
    assert out == ["subnet-abc", "subnet-01"]  # id passes through; name resolved


def test_resolve_subnet_ids_unknown_name_raises():
    class NoSubnets:
        def describe_subnets(self, **kw):
            return {"Subnets": []}

    with pytest.raises(RuntimeError):
        R.resolve_subnet_ids(NoSubnets(), ["not-a-real-subnet"], "vpc-1")


def test_delete_network_connector_tolerates_missing():
    class Gone:
        def delete_network_connector(self, **kw):
            raise ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "DeleteNetworkConnector",
            )

    R.delete_network_connector(Gone(), "arn:x")  # must not raise


# -- provisioner egress wiring ----------------------------------------------


class FakeIam:
    def create_role(self, **kw):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def get_role(self, **kw):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def put_role_policy(self, **kw):
        pass

    def attach_role_policy(self, **kw):
        pass


class _Paginator:
    def paginate(self, **kw):
        yield {"Versions": [], "DeleteMarkers": []}


class FakeS3:
    def create_bucket(self, **kw):
        pass

    def put_public_access_block(self, **kw):
        pass

    def put_bucket_versioning(self, **kw):
        pass

    def put_bucket_encryption(self, **kw):
        pass

    def upload_fileobj(self, fileobj, bucket, key):
        fileobj.read()

    def get_paginator(self, name):
        return _Paginator()


class FakeMv:
    def list_microvm_images(self, **kw):
        return {"items": []}

    def list_managed_microvm_images(self, **kw):
        return {"items": [{"imageArn": "arn:base", "createdAt": "2026"}]}

    def create_microvm_image(self, **kw):
        return {"imageArn": "arn:aws:lambda:us-east-1:1:microvm-image:agent-sandbox-guest"}

    def get_microvm_image(self, **kw):
        return {"latestActiveImageVersion": "1.0"}


class FakeSts:
    def get_caller_identity(self, **kw):
        return {"Account": "123456789012"}


class FakeEc2:
    def describe_security_groups(self, **kw):
        return {"SecurityGroups": []}

    def create_security_group(self, **kw):
        return {"GroupId": "sg-created"}

    def create_tags(self, **kw):
        pass

    def describe_subnets(self, **kw):
        # Resolve by tag:Name -> a fake id; otherwise a discovery result.
        for f in kw.get("Filters", []):
            if f["Name"] == "tag:Name":
                name = f["Values"][0]
                return {"Subnets": [{"SubnetId": f"subnet-{name[-2:]}"}]}
        return {"Subnets": [{"SubnetId": "subnet-disc"}]}


def _make_provisioner(tmp_path, clients):
    (tmp_path / "guest").mkdir()
    (tmp_path / "guest" / "Dockerfile").write_text("FROM scratch\n")
    cfg = InfraConfig.from_dict(
        {"project": "proj", "stack": "dev", "region": "us-east-1",
         "image": {"name": "agent-sandbox-guest", "guest_dir": "guest"}},
        base_dir=str(tmp_path),
    )
    store = InfraStateStore(path=tmp_path / "infra-state.json")
    prov = Provisioner(cfg, store=store)
    prov._clients = clients
    return prov, store, cfg


def test_up_creates_egress_connector(tmp_path):
    core = FakeCore()
    prov, store, cfg = _make_provisioner(
        tmp_path,
        {"iam": FakeIam(), "s3": FakeS3(), "lambda-microvms": FakeMv(),
         "sts": FakeSts(), "ec2": FakeEc2(), "lambda-core": core},
    )
    cfg.network.egress.vpc_id = "vpc-1"  # opt into create-from-scratch
    cfg.network.egress.subnet_ids = ["golden-subnet-01"]  # Name tag -> resolved

    outputs = prov.up()

    assert outputs["egress_network_connector_arn"].endswith(":nc-new")
    assert core.created is not None
    # Name tag was resolved to a subnet id before the API call.
    assert core.created["Configuration"]["VpcEgressConfiguration"]["SubnetIds"] == [
        "subnet-01"
    ]
    state = store.load("proj", "dev")
    assert state.get_resource("network_connector").managed is True
    assert state.get_resource("network_operator_role").managed is True


def test_up_reuses_egress_connector(tmp_path):
    prov, store, cfg = _make_provisioner(
        tmp_path,
        {"iam": FakeIam(), "s3": FakeS3(), "lambda-microvms": FakeMv(), "sts": FakeSts()},
    )
    cfg.network.egress.connector_arn = "arn:aws:lambda:us-east-1:1:network-connector:reused"

    outputs = prov.up()

    assert outputs["egress_network_connector_arn"].endswith(":reused")
    assert store.load("proj", "dev").get_resource("network_connector").managed is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
