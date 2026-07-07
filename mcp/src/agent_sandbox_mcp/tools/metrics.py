"""Metrics tools backed by CloudWatch (``AWS/LambdaMicroVMs``)."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import err, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry

_NAMESPACE = "AWS/LambdaMicroVMs"
_METRICS = ("CPUUtilization", "MemoryUtilization")


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    def _client(region: str | None):
        import boto3

        return boto3.client("cloudwatch", region_name=region or config.region)

    def _latest(client, microvm_id: str, minutes: int) -> dict:
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(minutes=minutes)
        out: dict = {}
        for metric in _METRICS:
            resp = client.get_metric_statistics(
                Namespace=_NAMESPACE,
                MetricName=metric,
                Dimensions=[{"Name": "MicrovmId", "Value": microvm_id}],
                StartTime=start,
                EndTime=end,
                Period=60,
                Statistics=["Average"],
            )
            points = sorted(resp.get("Datapoints", []), key=lambda p: p["Timestamp"])
            out[metric] = round(points[-1]["Average"], 2) if points else None
        return out

    @mcp.tool()
    @tool_handler
    async def sandbox_metrics(name: str) -> dict:
        """Get point-in-time CPU/memory metrics for one running sandbox."""
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        client = _client(rec.region)
        data = await asyncio.to_thread(_latest, client, rec.microvm_id, 30)
        return ok({"name": name, "microvm_id": rec.microvm_id, "metrics": data})

    @mcp.tool()
    @tool_handler
    async def sandbox_metrics_all() -> dict:
        """Get point-in-time metrics for all tracked sandboxes."""
        results = []
        for rec in registry.records():
            try:
                client = _client(rec.region)
                data = await asyncio.to_thread(_latest, client, rec.microvm_id, 30)
                results.append({"name": rec.name, "microvm_id": rec.microvm_id,
                                "metrics": data})
            except Exception as exc:  # noqa: BLE001
                results.append({"name": rec.name, "error": str(exc)})
        return ok({"sandboxes": results})

    @mcp.tool()
    @tool_handler
    async def sandbox_metrics_stream(
        name: str, samples: int = 5, interval_ms: int = 60000
    ) -> dict:
        """Collect a bounded number of metric samples from one sandbox.

        Polls `samples` times, sleeping `interval_ms` between polls, and returns
        the observed CPU/memory series.
        """
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        client = _client(rec.region)
        series = []
        n = max(1, min(samples, 60))
        for i in range(n):
            data = await asyncio.to_thread(_latest, client, rec.microvm_id, 5)
            series.append({"sample": i, "at": dt.datetime.now(dt.UTC).isoformat(),
                           "metrics": data})
            if i < n - 1:
                await asyncio.sleep(max(0, interval_ms) / 1000.0)
        return ok({"name": name, "samples": series})
