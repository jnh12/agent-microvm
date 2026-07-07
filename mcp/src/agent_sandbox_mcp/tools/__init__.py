"""Tool modules, grouped by category (mirrors microsandbox-mcp's grouping).

Each module exposes ``register(mcp, registry, config)`` which defines its tools
against a shared :class:`~mcp.server.fastmcp.FastMCP` instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_sandbox_mcp.tools import (
    execution,
    filesystem,
    images,
    lifecycle,
    logs,
    metrics,
    runtime,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.config import Config
    from agent_sandbox_mcp.session import SandboxRegistry

_MODULES = (runtime, lifecycle, execution, filesystem, logs, metrics, images)


def register_all(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    for module in _MODULES:
        module.register(mcp, registry, config)
