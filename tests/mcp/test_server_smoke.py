"""Stdio smoke test: launch the server and verify the tool/resource surface.

Spawns the real MCP server as a subprocess over stdio and drives the MCP
handshake. Does not touch AWS (only `tools/list`, `resources/list`, and a
`runtime_check` call that degrades gracefully without credentials).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "runtime_check",
    "runtime_install",
    "sandbox_run",
    "sandbox_create",
    "sandbox_start",
    "sandbox_list",
    "sandbox_status",
    "sandbox_inspect",
    "sandbox_stop",
    "sandbox_remove",
    "sandbox_wait",
    "sandbox_exec",
    "sandbox_shell",
    "sandbox_logs_read",
    "sandbox_logs_stream",
    "sandbox_fs_read",
    "sandbox_fs_write",
    "sandbox_fs_list",
    "sandbox_fs_mkdir",
    "sandbox_fs_remove",
    "sandbox_fs_copy",
    "sandbox_fs_rename",
    "sandbox_fs_stat",
    "sandbox_fs_exists",
    "sandbox_fs_copy_from_host",
    "sandbox_fs_copy_to_host",
    "sandbox_metrics",
    "sandbox_metrics_all",
    "sandbox_metrics_stream",
    "image_list",
    "image_inspect",
    "image_remove",
    "image_prune",
}

EXPECTED_RESOURCES = {
    "agent-sandbox://runtime",
    "agent-sandbox://sandboxes",
    "agent-sandbox://images",
    "agent-sandbox://policy",
    "agent-sandbox://schemas/sandbox-create",
}


async def _drive() -> tuple[set[str], set[str]]:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_sandbox_mcp"], env=os.environ
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            tool_names = {t.name for t in tools.tools}
            resource_uris = {str(r.uri) for r in resources.resources}
            return tool_names, resource_uris


def test_tool_and_resource_surface():
    tool_names, resource_uris = asyncio.run(_drive())
    missing_tools = EXPECTED_TOOLS - tool_names
    assert not missing_tools, f"missing tools: {sorted(missing_tools)}"
    missing_resources = EXPECTED_RESOURCES - resource_uris
    assert not missing_resources, f"missing resources: {sorted(missing_resources)}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
