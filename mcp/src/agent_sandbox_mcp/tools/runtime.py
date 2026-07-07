"""Runtime tools: verify the backend is usable and guide installation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import err, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry


def _check(config: Config) -> dict:
    result: dict = {
        "region": config.region,
        "boto3_available": False,
        "lambda_microvms_client": False,
        "aws_credentials": False,
        "image_arn": None,
        "role_arn": None,
        "ready": False,
    }
    try:
        import boto3

        result["boto3_available"] = True
        creds = boto3.Session(region_name=config.region).get_credentials()
        result["aws_credentials"] = creds is not None
    except Exception as exc:  # noqa: BLE001
        result["boto3_error"] = str(exc)

    try:
        from agent_sandbox.control_plane import ControlPlane

        cp = ControlPlane(region=config.region)
        # The op is present iff the botocore model knows lambda-microvms.
        result["lambda_microvms_client"] = hasattr(cp._client, "run_microvm")  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        result["lambda_microvms_error"] = str(exc)

    result["image_arn"] = config.image_arn()
    result["role_arn"] = config.role_arn()
    result["ready"] = bool(
        result["boto3_available"]
        and result["lambda_microvms_client"]
        and result["aws_credentials"]
        and result["image_arn"]
        and result["role_arn"]
    )
    return result


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.tool()
    @tool_handler
    async def runtime_check() -> dict:
        """Check whether the agent-sandbox backend is available.

        Verifies boto3, the AWS ``lambda-microvms`` control-plane client, AWS
        credentials, and whether image/role ARNs resolve (env or `asb infra`).
        """
        return ok(_check(config))

    @mcp.tool()
    @tool_handler
    async def runtime_install() -> dict:
        """Explain how to provision the backend.

        Unlike a local runtime, this backend runs on AWS Lambda MicroVMs and is
        provisioned with `asb infra up` (creates the image, execution role, and
        supporting resources). This tool does not perform installs itself.
        """
        state = _check(config)
        if state["ready"]:
            return ok({"message": "backend already provisioned", "runtime": state})
        return err(
            "Backend not provisioned. Run `asb infra up` (see repo README) to "
            "create the MicroVM image and execution role, or set "
            "$AGENT_SANDBOX_IMAGE_ARN and $AGENT_SANDBOX_EXECUTION_ROLE_ARN.",
            code="not_provisioned",
            runtime=state,
        )
