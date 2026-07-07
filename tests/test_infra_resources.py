"""Tests for the idempotent boto3 resource handlers (with lightweight fakes)."""

from __future__ import annotations

import io
import os
import zipfile

import pytest
from agent_sandbox.infra import archive
from agent_sandbox.infra import resources as R
from botocore.exceptions import ClientError


def _client_error(code: str, op: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


# -- IAM role ---------------------------------------------------------------


class FakeIam:
    def __init__(self, *, exists: bool = False):
        self.exists = exists
        self.calls: list[str] = []
        self.inline_policies: list[str] = []
        self.attached: list[str] = []

    def create_role(self, **kw):
        self.calls.append("create_role")
        if self.exists:
            raise _client_error("EntityAlreadyExists", "CreateRole")
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def get_role(self, **kw):
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kw['RoleName']}"}}

    def put_role_policy(self, **kw):
        self.calls.append("put_role_policy")
        self.inline_policies.append(kw["PolicyName"])

    def attach_role_policy(self, **kw):
        self.attached.append(kw["PolicyArn"])


def test_ensure_role_create_path():
    iam = FakeIam(exists=False)
    out = R.ensure_role(iam, name="agent-sandbox-exec", region="us-east-1",
                        account_id="123", extra_policy_arns=["arn:aws:iam::aws:policy/X"])
    assert out["arn"].endswith("role/agent-sandbox-exec")
    assert R.LOGS_POLICY_NAME in iam.inline_policies
    assert iam.attached == ["arn:aws:iam::aws:policy/X"]


def test_ensure_role_adopts_existing():
    iam = FakeIam(exists=True)  # create_role raises EntityAlreadyExists
    out = R.ensure_role(iam, name="agent-sandbox-exec", region="us-east-1",
                        account_id="123")
    assert out["arn"].endswith("role/agent-sandbox-exec")
    # policies still (re)applied idempotently
    assert R.LOGS_POLICY_NAME in iam.inline_policies


def test_delete_role_tolerates_missing():
    class Gone:
        def list_role_policies(self, **kw):
            raise _client_error("NoSuchEntity", "ListRolePolicies")

    R.delete_role(Gone(), "whatever")  # must not raise


# -- S3 bucket --------------------------------------------------------------


class FakeS3:
    def __init__(self, *, create_error: str | None = None):
        self.create_error = create_error
        self.calls: list[str] = []

    def create_bucket(self, **kw):
        self.calls.append("create_bucket")
        self.last_create_kwargs = kw
        if self.create_error:
            raise _client_error(self.create_error, "CreateBucket")

    def put_public_access_block(self, **kw):
        self.calls.append("pab")

    def put_bucket_versioning(self, **kw):
        self.calls.append("versioning")

    def put_bucket_encryption(self, **kw):
        self.calls.append("sse")


def test_ensure_bucket_us_east_1_omits_location():
    s3 = FakeS3()
    out = R.ensure_bucket(s3, name="b", region="us-east-1")
    assert out == {"name": "b", "arn": "arn:aws:s3:::b"}
    assert "CreateBucketConfiguration" not in s3.last_create_kwargs
    assert {"pab", "versioning", "sse"} <= set(s3.calls)


def test_ensure_bucket_other_region_sets_location():
    s3 = FakeS3()
    R.ensure_bucket(s3, name="b", region="us-west-2")
    assert s3.last_create_kwargs["CreateBucketConfiguration"] == {
        "LocationConstraint": "us-west-2"
    }


def test_ensure_bucket_tolerates_already_owned():
    s3 = FakeS3(create_error="BucketAlreadyOwnedByYou")
    out = R.ensure_bucket(s3, name="b", region="us-east-1")
    assert out["name"] == "b"  # did not raise


# -- MicroVM image ----------------------------------------------------------


class FakeMv:
    def __init__(self, *, active: bool):
        self.active = active
        self.created = False

    def list_microvm_images(self, **kw):
        item = {"name": "agent-sandbox-guest", "imageArn": "arn:img"}
        if self.active:
            item["latestActiveImageVersion"] = "1.0"
        return {"items": [item]}

    def get_microvm_image(self, **kw):
        return {"latestActiveImageVersion": "1.0"} if self.active else {}

    def list_managed_microvm_images(self, **kw):
        return {"items": [{"imageArn": "arn:base", "createdAt": "2026"}]}

    def create_microvm_image(self, **kw):
        self.created = True
        self.active = True  # becomes active for the subsequent poll
        return {"imageArn": "arn:img"}


def test_ensure_image_reuses_active():
    mv = FakeMv(active=True)
    out = R.ensure_image(mv, name="agent-sandbox-guest",
                        build_role_arn="arn:role", code_uri="s3://b/k")
    assert out["created"] is False
    assert out["arn"] == "arn:img"


def test_ensure_image_builds_when_absent():
    mv = FakeMv(active=False)
    out = R.ensure_image(mv, name="agent-sandbox-guest",
                        build_role_arn="arn:role", code_uri="s3://b/k")
    assert out["created"] is True
    assert mv.created is True


def test_ensure_image_rebuild_forces_build_even_when_active():
    mv = FakeMv(active=True)
    out = R.ensure_image(mv, name="agent-sandbox-guest",
                        build_role_arn="arn:role", code_uri="s3://b/k", rebuild=True)
    assert out["created"] is True


def test_delete_image_tolerates_missing():
    class Gone:
        def delete_microvm_image(self, **kw):
            raise _client_error("ResourceNotFoundException", "DeleteMicrovmImage")

    R.delete_image(Gone(), "arn:img")  # must not raise


def test_upload_guest_zips_and_uploads(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "app.py").write_text("print('hi')\n")

    class FakeS3Upload:
        def __init__(self):
            self.body = None

        def upload_fileobj(self, fileobj, bucket, key):
            self.bucket = bucket
            self.key = key
            self.body = fileobj.read()

    s3 = FakeS3Upload()
    uri = R.upload_guest(s3, bucket="b", key="microvm-images/x.zip",
                        guest_dir=str(tmp_path))
    assert uri == "s3://b/microvm-images/x.zip"
    assert s3.bucket == "b" and s3.key == "microvm-images/x.zip"
    assert s3.body and len(s3.body) > 0  # a non-empty zip was produced
    # A valid, extractable standard .zip round-trips both files.
    zf = zipfile.ZipFile(io.BytesIO(s3.body))
    assert set(zf.namelist()) == {"Dockerfile", "app.py"}
    assert zf.read("app.py") == b"print('hi')\n"


def test_zip_dir_produces_valid_extractable_zip(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "b.txt").write_text("beta\n")

    with archive.zip_dir(str(tmp_path)) as buf:
        data = buf.read()

    assert zipfile.is_zipfile(io.BytesIO(data))
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert set(zf.namelist()) == {"a.txt", os.path.join("pkg", "b.txt")}
    assert zf.read("a.txt") == b"alpha\n"
    assert zf.read(os.path.join("pkg", "b.txt")) == b"beta\n"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
