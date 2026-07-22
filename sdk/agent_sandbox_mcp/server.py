"""FastMCP server assembly and stdio entry point.

Builds the :class:`~mcp.server.fastmcp.FastMCP` app, wires the shared
:class:`~agent_sandbox_mcp.session.SandboxRegistry`, registers every tool module
and resource, and runs over stdio.

stdout is reserved for the MCP JSON-RPC stream, so all logging goes to stderr.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from agent_sandbox_mcp import resources
from agent_sandbox_mcp.config import load_config
from agent_sandbox_mcp.session import SandboxRegistry
from agent_sandbox_mcp.tools import register_all

logging.basicConfig(
    level=logging.INFO,
    format="[agent-sandbox-mcp] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("agent-sandbox-mcp")

INSTRUCTIONS = (
    "Isolated microVM sandboxes on AWS Lambda MicroVMs. Use these tools to run "
    "code and manage files INSIDE sandboxes instead of on the host: `sandbox_run` "
    "for one-off commands in an ephemeral VM; `sandbox_create` then `sandbox_exec`/"
    "`sandbox_shell` for persistent work; `sandbox_fs_*` for files. Image/role/region "
    "are auto-wired from `asb infra` outputs, so don't ask the user to paste them — "
    "call `infra_outputs` to pull the resolved values. Every tool returns a JSON "
    "envelope {ok, data|error}."
)


def build_server() -> FastMCP:
    config = load_config()
    registry = SandboxRegistry(config)

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        try:
            yield {"registry": registry, "config": config}
        finally:
            log.info("shutting down: closing sandbox clients")
            await registry.aclose()

    mcp = FastMCP("agent-sandbox", instructions=INSTRUCTIONS, lifespan=lifespan)
    register_all(mcp, registry, config)
    resources.register(mcp, registry, config)
    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
