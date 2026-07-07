"""Command execution tools: argv exec and shell string exec."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import exec_result, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.tool()
    @tool_handler
    async def sandbox_exec(
        name: str,
        command: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Execute an argv command inside a named sandbox.

        Runs `command` with `args` directly (no shell). Use `sandbox_shell` for
        pipes/redirects. Output is capped per the server's max-output policy.
        """
        sandbox = await registry.get(name)
        result = await sandbox.exec(
            command,
            args or [],
            cwd=cwd or config.workdir,
            env=env,
            timeout=timeout or config.default_timeout_seconds,
        )
        return ok(exec_result(result, config.max_output_bytes))

    @mcp.tool()
    @tool_handler
    async def sandbox_shell(
        name: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Execute a shell command string (`bash -lc`) inside a named sandbox.

        Pipes, redirects, `&&`, globs, and env expansion all work. Output is
        capped per the server's max-output policy.
        """
        sandbox = await registry.get(name)
        result = await sandbox.exec(
            "bash",
            ["-lc", command],
            cwd=cwd or config.workdir,
            env=env,
            timeout=timeout or config.default_timeout_seconds,
        )
        return ok(exec_result(result, config.max_output_bytes))
