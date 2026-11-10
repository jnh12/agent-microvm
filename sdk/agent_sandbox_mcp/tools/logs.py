"""Log tools backed by CloudWatch Logs (``/aws/lambda-microvms/<id>``)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import cap_text, err, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry


def _log_group(microvm_id: str) -> str:
    return f"/aws/lambda-microvms/{microvm_id}"


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    def _client(region: str | None):
        import boto3

        return boto3.client("logs", region_name=region or config.region)

    @mcp.tool()
    @tool_handler
    async def sandbox_logs_read(
        name: str,
        tail: int = 100,
        since_minutes: int | None = None,
        grep: str | None = None,
        log_group: str | None = None,
    ) -> dict:
        """Read recent captured logs for a sandbox.

        Returns up to `tail` latest events, optionally filtered to the last
        `since_minutes` and to lines containing `grep`. Output is capped.
        """
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        group = log_group or _log_group(rec.microvm_id)
        client = _client(rec.region)

        def _fetch() -> list[dict]:
            kwargs: dict = {"logGroupName": group, "limit": max(1, min(tail, 10000))}
            if since_minutes:
                kwargs["startTime"] = int((time.time() - since_minutes * 60) * 1000)
            resp = client.filter_log_events(**kwargs)
            return resp.get("events", [])

        events = await asyncio.to_thread(_fetch)
        lines = [e.get("message", "") for e in events]
        if grep:
            lines = [ln for ln in lines if grep in ln]
        text = "".join(lines) if any("\n" in ln for ln in lines) else "\n".join(lines)
        return ok({"log_group": group, "count": len(lines),
                   "logs": cap_text(text, config.max_output_bytes)})

    @mcp.tool()
    @tool_handler
    async def sandbox_logs_stream(
        name: str,
        cursor: str | None = None,
        follow_timeout_ms: int = 5000,
        log_group: str | None = None,
    ) -> dict:
        """Poll captured logs using a cursor and a bounded follow timeout.

        Pass the returned `cursor` back to fetch subsequent events. Waits up to
        `follow_timeout_ms` for new events before returning.
        """
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        group = log_group or _log_group(rec.microvm_id)
        client = _client(rec.region)
        deadline = time.monotonic() + max(0, follow_timeout_ms) / 1000.0

        def _poll(token: str | None) -> dict:
            kwargs: dict = {"logGroupName": group, "limit": 1000}
            if token:
                kwargs["nextToken"] = token
            else:
                kwargs["startTime"] = int((time.time() - 60) * 1000)
            return client.filter_log_events(**kwargs)

        token = cursor
        events: list[dict] = []
        while True:
            resp = await asyncio.to_thread(_poll, token)
            events = resp.get("events", [])
            token = resp.get("nextToken") or token
            if events or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        text = "\n".join(e.get("message", "") for e in events)
        return ok({"log_group": group, "count": len(events), "cursor": token,
                   "logs": cap_text(text, config.max_output_bytes)})
