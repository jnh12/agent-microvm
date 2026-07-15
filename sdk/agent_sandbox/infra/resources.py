"""Idempotent boto3 resource handlers for the infra provisioner.

Each ``ensure_*`` creates the resource if absent (or adopts an existing one with
the same name) and is safe to re-run; each ``delete_*`` tears it down. These are
pure functions over boto3 clients so they can be unit-tested with stubs/moto.

Covers the IAM role, S3 build bucket, guest object, MicroVM image, and egress
security group as direct API calls.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError

from agent_sandbox.infra import archive
from agent_sandbox.ports import HOOK_PORT

LOGS_POLICY_NAME = "agent-sandbox-logs"
S3_READ_POLICY_NAME = "agent-sandbox-s3-read"


def _default_hooks() -> dict[str, Any]:
    """The lifecycle hooks agentd implements, baked into the image at build.

    ``ready``/``validate`` ensure the snapshot captures a fully-booted, working
    agentd. The MicroVM hooks fire at runtime for per-VM uniqueness (``run``/
    ``resume``) and clean drain/flush (``suspend``/``terminate``). The port must
    match what the guest binds (``agentd.ports.HOOK_PORT``).
    """
    return {
        "port": HOOK_PORT,
        "microvmImageHooks": {
            "ready": "ENABLED",
            "readyTimeoutInSeconds": 60,
            "validate": "ENABLED",
            "validateTimeoutInSeconds": 15,
        },
        "microvmHooks": {
            "run": "ENABLED",
            "runTimeoutInSeconds": 5,
            "resume": "ENABLED",
            "resumeTimeoutInSeconds": 5,
            "suspend": "ENABLED",
            # Guest drains in-flight exec for AGENTD_SUSPEND_DRAIN_SECONDS (default
            # 8) before acking; keep this strictly larger. See agentd/hooks.py.
            "suspendTimeoutInSeconds": 10,
            "terminate": "ENABLED",
            "terminateTimeoutInSeconds": 10,
        },
    }

ASSUME_ROLE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


# -- IAM role ---------------------------------------------------------------


def _logs_policy(region: str, account_id: str) -> dict[str, Any]:
    arn = f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda-microvms/*"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "MicroVMLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": [arn, f"{arn}:*"],
            }
        ],
    }


def _s3_read_policy(bucket_arn: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadCodeArtifact",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"{bucket_arn}/*",
            },
            {
                "Sid": "ListCodeArtifactBucket",
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": bucket_arn,
            },
        ],
    }


def ensure_role(
    iam,
    *,
    name: str,
    region: str,
    account_id: str,
    extra_policy_arns: list[str] | None = None,
) -> dict[str, Any]:
    """Create (or adopt) the MicroVM execution role and (re)apply its policies.

    Idempotent: safe to re-run. Returns ``{"name", "arn"}``.
    """
    try:
        resp = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(ASSUME_ROLE_POLICY),
            Description="Execution role for agent-sandbox-os MicroVMs.",
        )
        arn = resp["Role"]["Arn"]
    except ClientError as exc:
        if _error_code(exc) != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]

    # Inline policies (create or overwrite -> idempotent).
    iam.put_role_policy(
        RoleName=name,
        PolicyName=LOGS_POLICY_NAME,
        PolicyDocument=json.dumps(_logs_policy(region, account_id)),
    )

    for policy_arn in extra_policy_arns or []:
        iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)

    _wait_role_exists(iam, name)
    return {"name": name, "arn": arn}


def attach_s3_read(iam, *, role_name: str, bucket_arn: str) -> None:
    """Grant the build role read access to the code-artifact bucket."""
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=S3_READ_POLICY_NAME,
        PolicyDocument=json.dumps(_s3_read_policy(bucket_arn)),
    )


def _wait_role_exists(iam, name: str, timeout: float = 30.0) -> None:
    """IAM is eventually consistent; wait until the role is retrievable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            iam.get_role(RoleName=name)
            return
        except ClientError as exc:
            if _error_code(exc) != "NoSuchEntity":
                raise
            time.sleep(1)


def delete_role(iam, name: str) -> None:
    """Detach/delete all policies then delete the role. Best-effort/idempotent."""
    try:
        inline = iam.list_role_policies(RoleName=name).get("PolicyNames", [])
        for pol in inline:
            iam.delete_role_policy(RoleName=name, PolicyName=pol)
        attached = iam.list_attached_role_policies(RoleName=name).get(
            "AttachedPolicies", []
        )
        for pol in attached:
            iam.detach_role_policy(RoleName=name, PolicyArn=pol["PolicyArn"])
        iam.delete_role(RoleName=name)
    except ClientError as exc:
        if _error_code(exc) != "NoSuchEntity":
            raise


# -- S3 build bucket --------------------------------------------------------


def ensure_bucket(s3, *, name: str, region: str) -> dict[str, Any]:
    """Create (or adopt) a private, versioned, encrypted build bucket."""
    try:
        kwargs: dict[str, Any] = {"Bucket": name}
        # us-east-1 must NOT send a LocationConstraint.
        if region and region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
    except ClientError as exc:
        if _error_code(exc) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise

    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_versioning(
        Bucket=name, VersioningConfiguration={"Status": "Enabled"}
    )
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    return {"name": name, "arn": f"arn:aws:s3:::{name}"}


def delete_bucket(s3, name: str) -> None:
    """Empty all object versions then delete the bucket. Best-effort/idempotent."""
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=name):
            to_delete = [
                {"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if to_delete:
                s3.delete_objects(Bucket=name, Delete={"Objects": to_delete})
        s3.delete_bucket(Bucket=name)
    except ClientError as exc:
        if _error_code(exc) not in ("NoSuchBucket", "404"):
            raise


def upload_guest(s3, *, bucket: str, key: str, guest_dir: str) -> str:
    """Zip ``guest_dir`` (ISA-L accelerated) and upload it; returns the ``s3://`` URI.

    Streams the archive from a spooled temp file via ``upload_fileobj`` so peak
    memory stays bounded and large images use multipart upload automatically.
    """
    with archive.zip_dir(guest_dir) as buf:
        s3.upload_fileobj(buf, bucket, key)
    return f"s3://{bucket}/{key}"


# -- MicroVM image ----------------------------------------------------------


def latest_base_image(mv) -> str:
    items = mv.list_managed_microvm_images(maxResults=50).get("items", [])
    if not items:
        raise RuntimeError(
            "No managed base MicroVM images available; set image.base_image_arn "
            "in sandbox.yaml."
        )
    items.sort(key=lambda i: str(i.get("createdAt") or ""), reverse=True)
    return items[0]["imageArn"]


def _image_active_version(mv, name: str) -> tuple[str, str] | None:
    """Return (image_arn, active_version) if an image ``name`` is active, else None."""
    arn = None
    try:
        for im in mv.list_microvm_images(maxResults=50).get("items", []):
            if im.get("name") == name:
                arn = im.get("imageArn")
                if im.get("latestActiveImageVersion"):
                    return arn, im["latestActiveImageVersion"]
                break
    except ClientError:
        return None
    if arn is None:
        return None
    gi = mv.get_microvm_image(imageIdentifier=arn)
    if gi.get("latestActiveImageVersion"):
        return arn, gi["latestActiveImageVersion"]
    return None


def ensure_image(
    mv,
    *,
    name: str,
    build_role_arn: str,
    code_uri: str,
    base_image_arn: str | None = None,
    base_image_version: str | None = None,
    rebuild: bool = False,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Create the MicroVM image (or reuse an existing active one) and wait active.

    When an image ``name`` already has an active version and ``rebuild`` is
    False, it is reused. With ``rebuild=True`` a new version is built.
    """
    if not rebuild:
        existing = _image_active_version(mv, name)
        if existing:
            arn, version = existing
            return {"arn": arn, "version": version, "created": False}

    base = base_image_arn or latest_base_image(mv)
    args: dict[str, Any] = {
        "name": name,
        "baseImageArn": base,
        "buildRoleArn": build_role_arn,
        "codeArtifact": {"uri": code_uri},
        "hooks": _default_hooks(),
    }
    if base_image_version:
        args["baseImageVersion"] = base_image_version

    resp = mv.create_microvm_image(**args)
    arn = resp["imageArn"]
    version = _wait_image_active(mv, arn, timeout=timeout)
    return {"arn": arn, "version": version, "created": True}


def _wait_image_active(mv, image_arn: str, timeout: float = 900.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = mv.get_microvm_image(imageIdentifier=image_arn)
        if resp.get("latestActiveImageVersion"):
            return resp["latestActiveImageVersion"]
        failed = resp.get("latestFailedImageVersion")
        if failed:
            raise RuntimeError(
                f"MicroVM image build failed: {_failure_reason(mv, image_arn, failed)}"
            )
        state = (resp.get("state") or "").upper()
        if state in {"FAILED", "ERROR"}:
            raise RuntimeError(f"MicroVM image build failed (state={state})")
        time.sleep(10)
    raise TimeoutError(
        f"Timed out waiting for MicroVM image {image_arn} to become active"
    )


def _failure_reason(mv, image_arn: str, version: str) -> str:
    try:
        v = mv.get_microvm_image_version(imageIdentifier=image_arn, imageVersion=version)
        return v.get("stateReason") or v.get("status") or "unknown"
    except ClientError:
        return "unknown"


def delete_image(mv, image_arn: str) -> None:
    try:
        mv.delete_microvm_image(imageIdentifier=image_arn)
    except ClientError as exc:
        if _error_code(exc) != "ResourceNotFoundException":
            raise


# -- Optional egress security group / VPC -----------------------------------


def default_vpc_id(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get(
        "Vpcs", []
    )
    if not vpcs:
        raise RuntimeError(
            "No default VPC found; set network.egress.vpc_id in sandbox.yaml."
        )
    return vpcs[0]["VpcId"]


def discover_subnets(ec2, vpc_id: str) -> list[str]:
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ).get("Subnets", [])
    return [s["SubnetId"] for s in subnets]


def resolve_subnet_ids(ec2, values: list[str], vpc_id: str) -> list[str]:
    """Map a mix of subnet IDs and ``Name`` tags to subnet IDs.

    Entries already shaped like ``subnet-...`` pass through; anything else is
    looked up by its ``Name`` tag within ``vpc_id``. Raises if a name has no
    match, so misconfiguration fails loudly instead of at the AWS API.
    """
    resolved: list[str] = []
    for value in values:
        if value.startswith("subnet-"):
            resolved.append(value)
            continue
        subnets = ec2.describe_subnets(
            Filters=[
                {"Name": "tag:Name", "Values": [value]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        ).get("Subnets", [])
        if not subnets:
            raise RuntimeError(
                f"No subnet named {value!r} found in {vpc_id}. Use a subnet id "
                "(subnet-...) or a valid Name tag in network.egress.subnet_ids."
            )
        resolved.extend(s["SubnetId"] for s in subnets)
    return resolved


def ensure_security_group(ec2, *, vpc_id: str, name: str = "agent-sandbox-egress") -> str:
    """Create (or adopt) an egress-only security group. Returns its id."""
    existing = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    ).get("SecurityGroups", [])
    if existing:
        return existing[0]["GroupId"]
    resp = ec2.create_security_group(
        GroupName=name,
        Description="Egress-only SG for agent-sandbox-os MicroVMs.",
        VpcId=vpc_id,
    )
    sg_id = resp["GroupId"]
    # New SGs already allow all egress by default; explicitly leave no ingress.
    ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "Name", "Value": name}])
    return sg_id


def delete_security_group(ec2, sg_id: str) -> None:
    try:
        ec2.delete_security_group(GroupId=sg_id)
    except ClientError as exc:
        if _error_code(exc) not in ("InvalidGroup.NotFound", "InvalidGroupId.NotFound"):
            raise


# -- VPC egress network connector (Lambda Network Connector) ----------------

NETWORK_CONNECTOR_OPERATOR_POLICY = "agent-sandbox-network-connector"

# Trust + permissions the operator role needs so Lambda can manage ENIs for a
# VPC_EGRESS connector in the caller's VPC.
_NETWORK_CONNECTOR_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ManageMicroVMNetworkInterfaces",
            "Effect": "Allow",
            "Action": [
                "ec2:CreateNetworkInterface",
                "ec2:CreateTags",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface",
            ],
            "Resource": "*",
        }
    ],
}


def ensure_network_connector_operator_role(
    iam, *, name: str, account_id: str
) -> dict[str, Any]:
    """Create (or adopt) the NetworkConnectorOperatorRole. Returns ``{name, arn}``.

    Idempotent: safe to re-run. Trusts ``lambda.amazonaws.com`` and grants the
    ENI-management permissions a VPC_EGRESS connector requires.
    """
    try:
        resp = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(ASSUME_ROLE_POLICY),
            Description="Operator role for agent-sandbox-os VPC egress connector.",
        )
        arn = resp["Role"]["Arn"]
    except ClientError as exc:
        if _error_code(exc) != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]

    iam.put_role_policy(
        RoleName=name,
        PolicyName=NETWORK_CONNECTOR_OPERATOR_POLICY,
        PolicyDocument=json.dumps(_NETWORK_CONNECTOR_POLICY),
    )
    _wait_role_exists(iam, name)
    return {"name": name, "arn": arn}


def _connector_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a ``lambda-core`` connector response (PascalCase API)."""
    arn = item.get("Arn") or item.get("networkConnectorArn") or item.get("arn")
    cid = item.get("Id") or item.get("networkConnectorId") or item.get("id")
    state = (item.get("State") or item.get("state") or "").upper()
    reason = item.get("StateReason") or item.get("stateReason") or ""
    return {"arn": arn, "id": cid, "state": state, "reason": reason}


def _find_network_connector(core, name: str) -> dict[str, Any] | None:
    """Return an existing connector matching ``name`` (adopt-on-reuse), else None."""
    lister = getattr(core, "list_network_connectors", None)
    if lister is None:
        return None
    try:
        resp = lister()
    except ClientError:
        return None
    items = resp.get("NetworkConnectors") or resp.get("items") or []
    for item in items:
        if item.get("Name") == name or item.get("name") == name:
            return _connector_fields(item)
    return None


def _is_assume_role_error(exc: ClientError) -> bool:
    """True for the transient 'service can't assume the operator role' error.

    A freshly created IAM role is not immediately assumable (IAM is eventually
    consistent), so CreateNetworkConnector can fail for a few seconds with an
    InvalidParameterValueException about assuming the operator role.
    """
    if _error_code(exc) not in ("InvalidParameterValueException", "AccessDenied"):
        return False
    return "assume" in str(exc).lower()


def ensure_network_connector(
    core,
    *,
    name: str,
    subnet_ids: list[str],
    security_group_ids: list[str],
    operator_role_arn: str,
    network_protocol: str = "IPv4",
    assume_retry_timeout: float = 120.0,
) -> dict[str, Any]:
    """Create (or adopt) a VPC_EGRESS network connector. Returns ``{arn, id, state}``.

    The ``lambda-core`` service is new; if the boto3 client lacks the operation,
    a clear error is raised so the user can update their AWS CLI / botocore.

    Retries the transient "unable to assume the operator role" error, which
    happens while a just-created operator role propagates through IAM.
    """
    existing = _find_network_connector(core, name)
    if existing and existing.get("arn"):
        return existing

    creator = getattr(core, "create_network_connector", None)
    if creator is None:
        raise RuntimeError(
            "boto3 'lambda-core' client has no 'create_network_connector' "
            "operation. Update AWS CLI v2 / botocore to a version that supports "
            "Lambda Network Connectors, or set network.egress.connector_arn to "
            "reuse an existing connector."
        )
    kwargs = {
        "Name": name,
        "Configuration": {
            "VpcEgressConfiguration": {
                "SubnetIds": subnet_ids,
                "SecurityGroupIds": security_group_ids,
                "NetworkProtocol": network_protocol,
                "AssociatedComputeResourceTypes": ["MicroVm"],
            }
        },
        "OperatorRole": operator_role_arn,
    }
    deadline = time.monotonic() + assume_retry_timeout
    attempt = 0
    while True:
        try:
            resp = creator(**kwargs)
            break
        except ClientError as exc:
            if _is_assume_role_error(exc) and time.monotonic() < deadline:
                time.sleep(min(2 ** attempt, 10))
                attempt += 1
                continue
            raise
    fields = _connector_fields(resp)
    if not fields.get("arn"):
        raise RuntimeError(f"create_network_connector returned no ARN: {resp!r}")
    return fields


def wait_network_connector_active(core, arn: str, timeout: float = 600.0) -> str:
    """Poll until the connector is ACTIVE (provisioning ENIs can take ~10 min)."""
    getter = getattr(core, "get_network_connector", None)
    if getter is None:
        return "UNKNOWN"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = getter(Identifier=arn)
        fields = _connector_fields(resp.get("networkConnector") or resp)
        state = fields["state"]
        if state == "ACTIVE":
            return state
        if state in {"FAILED", "ERROR"}:
            raise RuntimeError(
                f"Network connector {arn} failed: {fields['reason'] or 'unknown'}"
            )
        time.sleep(10)
    raise TimeoutError(
        f"Timed out waiting for network connector {arn} to become ACTIVE"
    )


def delete_network_connector(core, arn: str) -> None:
    """Delete a network connector. Best-effort/idempotent."""
    deleter = getattr(core, "delete_network_connector", None)
    if deleter is None:
        return
    try:
        deleter(Identifier=arn)
    except ClientError as exc:
        if _error_code(exc) not in ("ResourceNotFoundException", "NotFoundException"):
            raise


def wait_network_connector_deleted(core, arn: str, timeout: float = 600.0) -> None:
    """Wait until a deleting connector is fully gone (releases its ENIs)."""
    getter = getattr(core, "get_network_connector", None)
    if getter is None:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            getter(Identifier=arn)
        except ClientError as exc:
            if _error_code(exc) in ("ResourceNotFoundException", "NotFoundException"):
                return
            raise
        time.sleep(10)
    raise TimeoutError(f"Timed out waiting for network connector {arn} to delete")
