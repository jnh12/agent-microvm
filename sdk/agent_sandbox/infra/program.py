"""The Pulumi program: declares resources from an :class:`InfraConfig`.

``build(cfg)`` is the inline program run by the Automation API (see
:mod:`agent_sandbox.infra.runner`). It applies the reuse-or-create rules and
exports the outputs the SDK/CLI consume.
"""

from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws

from agent_sandbox.infra.components.iam import ExecutionRole
from agent_sandbox.infra.components.image import MicroVMImage
from agent_sandbox.infra.components.network import EgressSecurityGroup
from agent_sandbox.infra.components.storage import BuildBucket
from agent_sandbox.infra.config import InfraConfig


def build(cfg: InfraConfig) -> None:
    cfg.validate()

    # -- IAM role: reuse or create -------------------------------------
    role_component: ExecutionRole | None = None
    if cfg.role.reuse:
        role_arn: pulumi.Input[str] = cfg.role.arn
    else:
        role_component = ExecutionRole(
            "agent-sandbox",
            role_name=cfg.role.name or None,
            extra_policy_arns=cfg.role.extra_policy_arns,
        )
        role_arn = role_component.role_arn

    # -- S3 build bucket: reuse or create ------------------------------
    if cfg.bucket.reuse:
        bucket_name: pulumi.Input[str] = cfg.bucket.name
        bucket_arn: pulumi.Input[str] = f"arn:aws:s3:::{cfg.bucket.name}"
    else:
        bucket = BuildBucket("agent-sandbox")
        bucket_name = bucket.bucket.bucket
        bucket_arn = bucket.bucket.arn

    # -- Upload the guest image zip -------------------------------------
    guest_object = aws.s3.BucketObjectv2(
        "guest-archive",
        bucket=bucket_name,
        key=f"microvm-images/{cfg.image.name}.zip",
        source=pulumi.FileArchive(cfg.guest_dir_abs),
    )

    code_uri = pulumi.Output.concat("s3://", bucket_name, "/", guest_object.key)

    # -- Let the build role read the code artifact from S3 -------------
    # The build service assumes buildRoleArn to fetch codeArtifact; without this
    # the build fails with "Access denied when fetching artifact from S3". Only
    # attachable when we manage the role (a reused role must grant this itself).
    image_deps: list[pulumi.Resource] = []
    if role_component is not None:
        s3_read = aws.iam.RolePolicy(
            "agent-sandbox-s3-read",
            role=role_component.role.name,
            policy=pulumi.Output.from_input(bucket_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "ReadCodeArtifact",
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                                "Resource": f"{arn}/*",
                            },
                            {
                                "Sid": "ListCodeArtifactBucket",
                                "Effect": "Allow",
                                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                                "Resource": arn,
                            },
                        ],
                    }
                )
            ),
        )
        image_deps.append(s3_read)

    image = MicroVMImage(
        "agent-sandbox-guest",
        image_name=cfg.image.name,
        code_uri=code_uri,
        build_role_arn=role_arn,
        region=cfg.region,
        base_image_arn=cfg.image.base_image_arn or None,
        base_image_version=cfg.image.base_image_version or None,
        opts=pulumi.ResourceOptions(depends_on=image_deps) if image_deps else None,
    )

    # -- Optional VPC networking (sg_only model) ----------------------
    if cfg.network.enabled:
        vpc_id = cfg.network.vpc_id or aws.ec2.get_vpc(default=True).id

        if cfg.network.reuse_sg:
            security_group_id: pulumi.Input[str] = cfg.network.security_group_id
        else:
            sg = EgressSecurityGroup("agent-sandbox", vpc_id=vpc_id)
            security_group_id = sg.security_group_id

        subnet_ids = cfg.network.subnet_ids or aws.ec2.get_subnets(
            filters=[{"name": "vpc-id", "values": [vpc_id]}]
        ).ids

        pulumi.export("security_group_id", security_group_id)
        pulumi.export("subnet_ids", subnet_ids)
        pulumi.export("vpc_id", vpc_id)

    # -- Core outputs consumed by the SDK/CLI -------------------------
    pulumi.export("image_arn", image.image_arn)
    pulumi.export("execution_role_arn", role_arn)
    pulumi.export("build_bucket", bucket_name)
