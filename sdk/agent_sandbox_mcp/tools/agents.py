"""Hosted agents. Provider secrets are connected explicitly through the CLI."""

from __future__ import annotations

import asyncio

from agent_sandbox.agents import Agents
from agent_sandbox.hosted import enabled

from agent_sandbox_mcp.envelope import ok, tool_handler


def register(mcp, registry, config):
    if not enabled():
        return

    async def invoke(method, *args, **kwargs):
        return ok(await asyncio.to_thread(getattr(Agents(), method), *args, **kwargs))

    @mcp.tool()
    @tool_handler
    async def agent_resources(resource: str, limit: int = 100, offset: int = 0) -> dict:
        """List hosted profiles, credential metadata, connections, agents, runs or webhooks."""
        return await invoke("list", resource, limit=limit, offset=offset)

    @mcp.tool()
    @tool_handler
    async def agent_inspect(resource: str, resource_id: str) -> dict:
        """Inspect a hosted profile, connection, agent, run or webhook."""
        return await invoke("get", resource, resource_id)

    @mcp.tool()
    @tool_handler
    async def agent_profile_create(
        name: str,
        provider: str,
        credential_id: str,
        connection_ids: list[str] | None = None,
        model: str | None = None,
        instructions: str = "",
        max_run_seconds: int = 600,
    ) -> dict:
        """Create a profile with an existing credential and explicitly selected connections."""
        fields = dict(
            name=name,
            provider=provider,
            credential_id=credential_id,
            connection_ids=connection_ids or [],
            instructions=instructions,
            max_run_seconds=max_run_seconds,
        )
        if model:
            fields["model"] = model
        return await invoke("create", "profiles", **fields)

    @mcp.tool()
    @tool_handler
    async def agent_create(name: str, profile_id: str) -> dict:
        """Create a durable agent; its files and conversation survive stop/start."""
        return await invoke("create", "agents", name=name, profile_id=profile_id)

    @mcp.tool()
    @tool_handler
    async def agent_action(agent_id: str, action: str) -> dict:
        """Start or stop a durable agent. Stop checkpoints files, not running processes."""
        return await invoke("action", "agents", agent_id, action)

    @mcp.tool()
    @tool_handler
    async def agent_run(
        prompt: str,
        profile_id: str | None = None,
        agent_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Queue a prompt. Select a profile for a disposable job or an existing durable agent."""
        return await invoke(
            "run", prompt, profile_id=profile_id, agent_id=agent_id, idempotency_key=idempotency_key
        )

    @mcp.tool()
    @tool_handler
    async def agent_message(agent_id: str, text: str, idempotency_key: str | None = None) -> dict:
        """Continue a durable agent conversation; returns a run ID immediately."""
        return await invoke("message", agent_id, text, idempotency_key=idempotency_key)

    @mcp.tool()
    @tool_handler
    async def agent_run_events(run_id: str, after: int = 0) -> dict:
        """Read bounded run events after a cursor; retain next_cursor for the next poll."""
        return await invoke("events", run_id, after=after)

    @mcp.tool()
    @tool_handler
    async def agent_run_cancel(run_id: str) -> dict:
        """Cancel a hosted agent run and revoke its runtime capability."""
        return await invoke("action", "runs", run_id, "cancel")

    @mcp.tool()
    @tool_handler
    async def agent_run_artifacts(run_id: str) -> dict:
        """List files exported by a hosted agent run."""
        return await invoke("artifacts", run_id)
