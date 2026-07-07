"""MicroVM image resource.

There is no native Pulumi resource for Lambda MicroVMs yet, so the image is
managed via a dynamic provider that calls the ``lambda-microvms`` boto3 client.

The GA ``create_microvm_image`` schema builds your image on top of a managed
**base image** (e.g. ``al2023``):

- ``name`` - image name
- ``baseImageArn`` (+ optional ``baseImageVersion``) - managed base image
- ``buildRoleArn`` - role Lambda assumes to build the image
- ``codeArtifact.uri`` - ``s3://bucket/key`` of the packaged code

The provider polls ``get_microvm_image`` until an active version appears.
"""

from __future__ import annotations

import time
from typing import Any

import pulumi
from pulumi.dynamic import CreateResult, Resource, ResourceProvider


def _client(region: str | None):
    import boto3

    return boto3.client("lambda-microvms", region_name=region)


def _latest_base_image(client) -> str:
    items = client.list_managed_microvm_images(maxResults=50).get("items", [])
    if not items:
        raise Exception(
            "No managed base MicroVM images available; set image.base_image_arn in setup.yaml."
        )
    items.sort(key=lambda i: str(i.get("createdAt") or ""), reverse=True)
    return items[0]["imageArn"]


class _MicroVMImageProvider(ResourceProvider):
    def create(self, props: dict[str, Any]) -> CreateResult:
        client = _client(props.get("region"))

        base_image_arn = props.get("base_image_arn") or _latest_base_image(client)
        args: dict[str, Any] = {
            "name": props["name"],
            "baseImageArn": base_image_arn,
            "buildRoleArn": props["build_role_arn"],
            "codeArtifact": {"uri": props["code_uri"]},
        }
        if props.get("base_image_version"):
            args["baseImageVersion"] = props["base_image_version"]

        resp = client.create_microvm_image(**args)
        image_arn = resp["imageArn"]
        image_version = resp.get("imageVersion")

        self._wait_ready(client, image_arn)

        return CreateResult(
            id_=image_arn,
            outs={
                **props,
                "image_arn": image_arn,
                "image_version": image_version,
                "base_image_arn": base_image_arn,
            },
        )

    def delete(self, id_: str, props: dict[str, Any]) -> None:
        client = _client(props.get("region"))
        try:
            client.delete_microvm_image(imageIdentifier=id_)
        except Exception:  # noqa: BLE001 - deletion is best-effort
            pass

    @staticmethod
    def _wait_ready(client, image_arn: str, timeout: float = 900.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = client.get_microvm_image(imageIdentifier=image_arn)
            state = (resp.get("state") or "").upper()
            if resp.get("latestActiveImageVersion"):
                return
            if resp.get("latestFailedImageVersion"):
                reason = _failure_reason(
                    client, image_arn, resp["latestFailedImageVersion"]
                )
                raise Exception(f"MicroVM image build failed: {reason}")
            if state in {"FAILED", "ERROR"}:
                raise Exception(f"MicroVM image build failed (state={state})")
            time.sleep(10)
        raise Exception(f"Timed out waiting for MicroVM image {image_arn} to become active")


def _failure_reason(client, image_arn: str, version: str) -> str:
    try:
        v = client.get_microvm_image_version(imageIdentifier=image_arn, imageVersion=version)
        return v.get("stateReason") or v.get("status") or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


class MicroVMImage(Resource):
    image_arn: pulumi.Output[str]
    image_version: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        *,
        image_name: pulumi.Input[str],
        code_uri: pulumi.Input[str],
        build_role_arn: pulumi.Input[str],
        region: pulumi.Input[str],
        base_image_arn: pulumi.Input[str] | None = None,
        base_image_version: pulumi.Input[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__(
            _MicroVMImageProvider(),
            name,
            {
                "name": image_name,
                "code_uri": code_uri,
                "build_role_arn": build_role_arn,
                "region": region,
                "base_image_arn": base_image_arn or "",
                "base_image_version": base_image_version or "",
                "image_arn": None,
                "image_version": None,
            },
            opts,
        )
