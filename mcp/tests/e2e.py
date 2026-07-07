"""Live end-to-end exercise of the MCP server against real AWS infra.

Gated by ``AGENT_SANDBOX_MCP_E2E=1`` (and requires deployed infra + credentials).
Runs the server over stdio and drives a realistic flow: runtime check, an
ephemeral ``sandbox_run``, then a persistent create -> write -> read -> shell ->
remove cycle.

    AGENT_SANDBOX_MCP_E2E=1 uv run python mcp/tests/e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _envelope(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("no content in tool result")


async def _call(session: ClientSession, name: str, **args) -> dict:
    print(f"-> {name}({args})")
    env = _envelope(await session.call_tool(name, args))
    print(f"<- {json.dumps(env, default=str)[:400]}")
    assert env.get("ok"), f"{name} failed: {env}"
    return env["data"]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_sandbox_mcp"],
        env={**os.environ, "PYTHONPATH": SRC},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            runtime = _envelope(await session.call_tool("runtime_check", {}))
            print("runtime:", json.dumps(runtime, default=str))
            assert runtime["ok"] and runtime["data"]["ready"], "backend not ready"

            run = await _call(session, "sandbox_run", command="echo hello-from-e2e")
            assert "hello-from-e2e" in run["stdout"]["text"]

            name = "mcp-e2e"
            try:
                await _call(session, "sandbox_create", name=name)
                await _call(
                    session, "sandbox_fs_write", name=name, path="note.txt",
                    content="e2e-content",
                )
                read_back = await _call(
                    session, "sandbox_fs_read", name=name, path="note.txt"
                )
                assert read_back["content"]["text"] == "e2e-content"
                await _call(session, "sandbox_fs_list", name=name)
                sh = await _call(
                    session, "sandbox_shell", name=name, command="cat note.txt"
                )
                assert "e2e-content" in sh["stdout"]["text"]
                await _call(session, "sandbox_metrics", name=name)
            finally:
                await _call(session, "sandbox_remove", name=name)

    print("\nE2E PASSED")


if __name__ == "__main__":
    if os.environ.get("AGENT_SANDBOX_MCP_E2E") not in {"1", "true", "yes"}:
        print("skipping e2e: set AGENT_SANDBOX_MCP_E2E=1 to run (needs live infra).")
        sys.exit(0)
    asyncio.run(main())
