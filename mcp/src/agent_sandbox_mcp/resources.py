"""MCP resources under the ``agent-sandbox://`` URI scheme."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry

SANDBOX_CREATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "sandbox_create",
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "maxLength": 128},
        "image": {"type": ["string", "null"], "description": "MicroVM image ARN."},
        "role": {"type": ["string", "null"], "description": "Execution role ARN."},
        "region": {"type": ["string", "null"]},
        "cpus": {"type": "integer", "default": 1},
        "memory": {"type": "integer", "default": 512, "description": "MB"},
        "egress_connector": {
            "type": ["string", "null"],
            "description": "VPC egress network connector ARN to attach.",
        },
        "egress": {
            "type": "boolean",
            "default": False,
            "description": "Attach the egress connector from setup.yaml / asb infra outputs.",
        },
    },
    "additionalProperties": False,
}


def _dumps(data: object) -> str:
    return json.dumps(data, indent=2, default=str)


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.resource("agent-sandbox://runtime")
    async def runtime_resource() -> str:
        """Runtime/config status for the agent-sandbox backend."""
        from agent_sandbox_mcp.tools.runtime import _check

        return _dumps(_check(config))

    @mcp.resource("agent-sandbox://sandboxes")
    async def sandboxes_resource() -> str:
        """Current inventory of locally tracked sandboxes."""
        records = [
            {
                "name": r.name,
                "microvm_id": r.microvm_id,
                "endpoint": r.endpoint,
                "region": r.region,
                "image_arn": r.image_arn,
            }
            for r in registry.records()
        ]
        return _dumps({"sandboxes": records, "count": len(records)})

    @mcp.resource("agent-sandbox://images")
    async def images_resource() -> str:
        """Current account MicroVM image inventory."""
        cp = registry.control_plane()
        images = await cp.list_microvm_images()
        return _dumps({"images": images, "count": len(images)})

    @mcp.resource("agent-sandbox://policy")
    async def policy_resource() -> str:
        """Effective host-path and dangerous-operation policy."""
        return _dumps(
            {
                "host_path_policy": config.host_path_policy,
                "host_paths": config.host_paths,
                "enable_dangerous": config.enable_dangerous,
                "max_output_bytes": config.max_output_bytes,
                "default_timeout_ms": config.default_timeout_ms,
            }
        )

    @mcp.resource("agent-sandbox://schemas/sandbox-create")
    async def sandbox_create_schema_resource() -> str:
        """JSON Schema for `sandbox_create` inputs."""
        return _dumps(SANDBOX_CREATE_SCHEMA)
