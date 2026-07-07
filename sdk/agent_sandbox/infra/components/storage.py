"""S3 build bucket used to hand the guest image zip to Lambda MicroVMs."""

from __future__ import annotations

import pulumi
import pulumi_aws as aws


class BuildBucket(pulumi.ComponentResource):
    """A private, versioned S3 bucket for MicroVM image build artifacts."""

    bucket: aws.s3.BucketV2

    def __init__(self, name: str, opts: pulumi.ResourceOptions | None = None) -> None:
        super().__init__("agent-sandbox:infra:BuildBucket", name, None, opts)

        self.bucket = aws.s3.BucketV2(
            f"{name}-bucket",
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.s3.BucketVersioningV2(
            f"{name}-versioning",
            bucket=self.bucket.id,
            versioning_configuration={"status": "Enabled"},
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.s3.BucketPublicAccessBlock(
            f"{name}-pab",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.s3.BucketServerSideEncryptionConfigurationV2(
            f"{name}-sse",
            bucket=self.bucket.id,
            rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({"bucket_name": self.bucket.bucket})
