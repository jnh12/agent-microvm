"""Tests for the Provisioner orchestration (reuse-or-create + managed teardown)."""

from __future__ import annotations

import pytest
from agent_sandbox.infra.config import ImageConfig, InfraConfig, RoleConfig
from agent_sandbox.infra.provisioner import Provisioner
from agent_sandbox.infra.state import InfraStateStore

# -- fakes ------------------------------------------------------------------


class FakeIam:
    def __init__(self):
        self.deleted = False

    def create_role(self, **kw):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def get_role(self, **kw):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def put_role_policy(self, **kw):
        pass

    def attach_role_policy(self, **kw):
        pass

    def list_role_policies(self, **kw):
        return {"PolicyNames": []}

    def list_attached_role_policies(self, **kw):
        return {"AttachedPolicies": []}

    def delete_role(self, **kw):
        self.deleted = True


class _Paginator:
    def paginate(self, **kw):
        yield {"Versions": [], "DeleteMarkers": []}


class FakeS3:
    def __init__(self):
        self.deleted = False

    def create_bucket(self, **kw):
        pass

    def put_public_access_block(self, **kw):
        pass

    def put_bucket_versioning(self, **kw):
        pass

    def put_bucket_encryption(self, **kw):
        pass

    def put_object(self, **kw):
        pass

    def upload_fileobj(self, fileobj, bucket, key):
        fileobj.read()

    def get_paginator(self, name):
        return _Paginator()

    def delete_objects(self, **kw):
        pass

    def delete_bucket(self, **kw):
        self.deleted = True


class FakeMv:
    def __init__(self):
        self.deleted = False

    def list_microvm_images(self, **kw):
        return {"items": []}  # nothing active -> build path

    def list_managed_microvm_images(self, **kw):
        return {"items": [{"imageArn": "arn:base", "createdAt": "2026"}]}

    def create_microvm_image(self, **kw):
        return {"imageArn": "arn:aws:lambda:us-east-1:1:microvm-image:agent-sandbox-guest"}

    def get_microvm_image(self, **kw):
        return {"latestActiveImageVersion": "1.0"}

    def delete_microvm_image(self, **kw):
        self.deleted = True


class FakeSts:
    def get_caller_identity(self, **kw):
        return {"Account": "123456789012"}


def _make_provisioner(tmp_path, clients):
    (tmp_path / "guest").mkdir()
    (tmp_path / "guest" / "Dockerfile").write_text("FROM scratch\n")
    cfg = InfraConfig(
        project="proj",
        stack="dev",
        region="us-east-1",
        image=ImageConfig(name="agent-sandbox-guest", guest_dir="guest"),
        role=RoleConfig(name="agent-sandbox-exec"),
        base_dir=str(tmp_path),
    )
    store = InfraStateStore(path=tmp_path / "infra-state.json")
    prov = Provisioner(cfg, store=store)
    prov._clients = clients  # inject fakes; _client() returns these
    return prov, store, cfg


def test_up_create_path_records_managed_and_outputs(tmp_path):
    iam, s3, mv, sts = FakeIam(), FakeS3(), FakeMv(), FakeSts()
    prov, store, cfg = _make_provisioner(
        tmp_path, {"iam": iam, "s3": s3, "lambda-microvms": mv, "sts": sts}
    )

    outputs = prov.up()

    assert outputs["execution_role_arn"].endswith("role/agent-sandbox-exec")
    assert outputs["image_arn"].endswith("microvm-image:agent-sandbox-guest")
    assert outputs["build_bucket"].startswith("proj-dev-")

    state = store.load("proj", "dev")
    assert state.get_resource("role").managed is True
    assert state.get_resource("bucket").managed is True
    assert state.get_resource("image").managed is True


def test_up_reuse_role_marks_unmanaged(tmp_path):
    iam, s3, mv, sts = FakeIam(), FakeS3(), FakeMv(), FakeSts()
    prov, store, cfg = _make_provisioner(
        tmp_path, {"iam": iam, "s3": s3, "lambda-microvms": mv, "sts": sts}
    )
    cfg.role = RoleConfig(arn="arn:aws:iam::1:role/preexisting")

    outputs = prov.up()
    assert outputs["execution_role_arn"] == "arn:aws:iam::1:role/preexisting"
    assert store.load("proj", "dev").get_resource("role").managed is False


def test_destroy_only_removes_managed(tmp_path):
    iam, s3, mv, sts = FakeIam(), FakeS3(), FakeMv(), FakeSts()
    prov, store, cfg = _make_provisioner(
        tmp_path, {"iam": iam, "s3": s3, "lambda-microvms": mv, "sts": sts}
    )

    # Seed: managed role/bucket/image, but a *reused* (unmanaged) role scenario.
    state = store.load("proj", "dev")
    state.set_resource("role", "iam-role", "agent-sandbox-exec", managed=False)
    state.set_resource("bucket", "s3-bucket", "b", managed=True)
    state.set_resource("image", "microvm-image", "arn:img", managed=True)
    store.save(state)

    prov.destroy()

    assert iam.deleted is False  # reused role left untouched
    assert s3.deleted is True    # managed bucket deleted
    assert mv.deleted is True    # managed image deleted
    assert store.load("proj", "dev").resources == {}  # state cleared


def test_read_outputs_best_effort(tmp_path, monkeypatch):
    from agent_sandbox.infra import provisioner as P

    monkeypatch.setenv("AGENT_SANDBOX_INFRA_STATE", str(tmp_path / "s.json"))
    cfg = InfraConfig(project="proj", stack="dev")
    assert P.read_outputs(cfg) == {}  # no state yet -> empty, never raises


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
