"""Infra tools: surface `asb infra` outputs so sandbox details need not be pasted.

`sandbox_create` / `sandbox_run` already fall back to these outputs when the
`image` / `role` / `egress` arguments are omitted (see
:mod:`agent_sandbox_mcp.session`), but the model can't *see* that wiring — so it
tends to ask the user to paste `asb infra` output. `infra_outputs` makes the
resolved values explicit, including the **project name** they belong to, so that
with several `sandbox.yaml` files (each its own project) the model can pull the
right image ARN / execution role / region for the project it wants and pass them
to `sandbox_create`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent_sandbox.sandbox import ENV_IMAGE_ARN, ENV_ROLE_ARN

from agent_sandbox_mcp.config import ENV_EGRESS_CONNECTOR, Config
from agent_sandbox_mcp.envelope import ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry


def _resolved(env_var: str, infra_value: str | None) -> dict:
    """Report a value plus where it came from (env var wins over infra state)."""
    env_value = os.environ.get(env_var)
    if env_value:
        return {"value": env_value, "source": "env"}
    if infra_value:
        return {"value": infra_value, "source": "infra"}
    return {"value": None, "source": "unset"}


def _resolve_infra(
    setup_file: str | None = None,
    project: str | None = None,
    stack: str | None = None,
) -> tuple[str | None, str | None, dict]:
    """Best-effort ``(project, stack, outputs)`` for the selected infra stack.

    Selection precedence:

    * ``project`` (with optional ``stack``) reads that stack straight from the
      infra state store — no ``sandbox.yaml`` needed.
    * ``setup_file`` loads that specific config file and reads its stack.
    * Otherwise the default ``sandbox.yaml`` (or ``$AGENT_SANDBOX_SETUP``) is used.

    Never raises: returns ``(None, None, {})`` when nothing resolves, so callers
    still fall back to env vars.
    """
    try:
        from agent_sandbox.infra.config import InfraConfig
        from agent_sandbox.infra.state import InfraStateStore

        if project:
            resolved_stack = stack or InfraConfig().stack
            outputs = InfraStateStore().load(project, resolved_stack).outputs
            return project, resolved_stack, outputs

        from agent_sandbox.infra.config import load_setup
        from agent_sandbox.infra.provisioner import read_outputs

        cfg = load_setup(setup_file)
        if stack:
            cfg.stack = stack
        return cfg.project, cfg.stack, read_outputs(cfg)
    except Exception:  # noqa: BLE001 - selection is best-effort; env vars still work
        return project, stack, {}


def build_outputs(
    config: Config,
    setup_file: str | None = None,
    project: str | None = None,
    stack: str | None = None,
) -> dict:
    """Assemble the resolved infra outputs the sandbox tools would use."""
    proj, stk, outputs = _resolve_infra(setup_file, project, stack)
    image = _resolved(ENV_IMAGE_ARN, outputs.get("image_arn"))
    role = _resolved(ENV_ROLE_ARN, outputs.get("execution_role_arn"))
    egress = _resolved(ENV_EGRESS_CONNECTOR, outputs.get("egress_network_connector_arn"))
    return {
        # Ready to create a sandbox without any user-provided ARNs.
        "ready": bool(image["value"] and role["value"]),
        "project": proj,
        "stack": stk,
        "region": config.region,
        "image_arn": image,
        "role_arn": role,
        "egress_connector_arn": egress,
        "build_bucket": outputs.get("build_bucket"),
        "network": {
            "vpc_id": outputs.get("vpc_id"),
            "subnet_ids": outputs.get("subnet_ids") or [],
            "security_group_id": outputs.get("security_group_id"),
        },
        "raw_infra_outputs": outputs,
    }


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.tool()
    @tool_handler
    async def infra_outputs(
        setup_file: str | None = None,
        project: str | None = None,
        stack: str | None = None,
    ) -> dict:
        """Pull sandbox provisioning details from `asb infra` outputs.

        Call this before `sandbox_create` / `sandbox_run` when the user hasn't
        supplied an image ARN, execution role, region, or egress connector — the
        values are auto-wired from `asb infra up` (or the corresponding env vars),
        so there's no need to ask the user to paste them.

        With multiple `sandbox.yaml` files (one project each), target a specific
        stack with `project` (optionally `stack`), or point `setup_file` at a
        config path. The result names the `project`/`stack` the ARNs belong to.
        Each field reports its `source` (`env`, `infra`, or `unset`); `ready` is
        true when both image and role resolve, so `sandbox_create` needs no ARNs.
        Use `infra_list` to discover which projects are provisioned.
        """
        return ok(build_outputs(config, setup_file, project, stack))

    @mcp.tool()
    @tool_handler
    async def infra_list() -> dict:
        """List every provisioned infra stack (project/stack) in local state.

        Use this to discover which `sandbox.yaml` projects have been brought up
        with `asb infra up`, then call `infra_outputs(project=...)` to fetch the
        image/role/region for the one you want.
        """
        from agent_sandbox.infra.state import InfraStateStore

        stacks = [
            {"project": proj, "stack": stk}
            for proj, stk in InfraStateStore().list_stacks()
        ]
        return ok({"stacks": stacks, "count": len(stacks)})
