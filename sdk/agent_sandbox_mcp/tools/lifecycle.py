"""Sandbox lifecycle tools: run, create, start, list, status, inspect, stop, remove, wait."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from agent_sandbox import Sandbox
from agent_sandbox.models import SandboxState

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import err, exec_result, ok, tool_handler
from agent_sandbox_mcp.session import resolve_egress_connectors

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry


def _record_summary(rec, status: str | None = None) -> dict:
    return {
        "name": rec.name,
        "microvm_id": rec.microvm_id,
        "endpoint": rec.endpoint,
        "image_arn": rec.image_arn,
        "region": rec.region,
        "status": status,
    }


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    @mcp.tool()
    @tool_handler
    async def sandbox_run(
        command: str,
        image: str | None = None,
        role: str | None = None,
        region: str | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
        egress_connector: str | None = None,
        egress: bool = False,
    ) -> dict:
        """Create an ephemeral sandbox, run a shell command, and remove it.

        Boots a throwaway MicroVM, runs `command` via `bash -lc`, returns the
        output, then terminates the VM. Nothing is tracked in local state.

        Set `egress_connector` to attach a specific VPC egress connector ARN, or
        `egress=true` to use the one from setup.yaml / `asb infra` outputs.
        """
        name = f"mcp-run-{secrets.token_hex(4)}"
        egress_connectors = resolve_egress_connectors(config, egress_connector, egress)
        sandbox = await registry.create_ephemeral(
            name, image=image, role=role, region=region,
            egress_connectors=egress_connectors,
        )
        try:
            result = await sandbox.exec(
                "bash",
                ["-lc", command],
                cwd=cwd or config.workdir,
                timeout=timeout or config.default_timeout_seconds,
            )
            payload = exec_result(result, config.max_output_bytes)
            payload["microvm_id"] = sandbox.microvm_id
            return ok(payload)
        finally:
            try:
                await sandbox.terminate()
            finally:
                registry.forget(name)

    @mcp.tool()
    @tool_handler
    async def sandbox_create(
        name: str,
        image: str | None = None,
        role: str | None = None,
        region: str | None = None,
        cpus: int = 1,
        memory: int = 512,
        egress_connector: str | None = None,
        egress: bool = False,
    ) -> dict:
        """Create and boot a persistent, named sandbox tracked in local state.

        The sandbox is reachable by `name` from other tools and from the `asb`
        CLI until `sandbox_remove` terminates it.

        Set `egress_connector` to attach a specific VPC egress connector ARN, or
        `egress=true` to use the one from setup.yaml / `asb infra` outputs.
        """
        egress_connectors = resolve_egress_connectors(config, egress_connector, egress)
        sandbox = await registry.create_named(
            name, image=image, role=role, region=region, cpus=cpus, memory=memory,
            egress_connectors=egress_connectors,
        )
        return ok(
            {
                "name": name,
                "microvm_id": sandbox.microvm_id,
                "endpoint": sandbox.endpoint,
                "state": str(sandbox.state),
            }
        )

    @mcp.tool()
    @tool_handler
    async def sandbox_start(name: str) -> dict:
        """Resume a stopped (suspended) sandbox and reconnect its agent."""
        sandbox = await registry.get(name)
        await sandbox.start()
        await registry.refresh_record(name)
        return ok(
            {
                "name": name,
                "microvm_id": sandbox.microvm_id,
                "endpoint": sandbox.endpoint,
                "state": str(sandbox.state),
            }
        )

    @mcp.tool()
    @tool_handler
    async def sandbox_list() -> dict:
        """List locally tracked sandboxes with their current status."""
        records = registry.records()
        out = []
        for rec in records:
            status = None
            try:
                cp = registry.control_plane(rec.region)
                info = await cp.get_microvm(rec.microvm_id)
                status = info.status
            except Exception:  # noqa: BLE001 - status is best-effort
                status = "unreachable"
            out.append(_record_summary(rec, status))
        return ok({"sandboxes": out, "count": len(out)})

    @mcp.tool()
    @tool_handler
    async def sandbox_status(name: str | None = None) -> dict:
        """Show status for one sandbox (by name) or all tracked sandboxes."""
        if name is not None:
            rec = registry.store.get(name)
            if rec is None:
                return err(f"no sandbox named {name!r}", code="not_found")
            cp = registry.control_plane(rec.region)
            info = await cp.get_microvm(rec.microvm_id)
            return ok(_record_summary(rec, info.status))
        results = []
        for rec in registry.records():
            try:
                cp = registry.control_plane(rec.region)
                info = await cp.get_microvm(rec.microvm_id)
                results.append(_record_summary(rec, info.status))
            except Exception:  # noqa: BLE001
                results.append(_record_summary(rec, "unreachable"))
        return ok({"sandboxes": results})

    @mcp.tool()
    @tool_handler
    async def sandbox_inspect(name: str) -> dict:
        """Return full control-plane configuration/metadata for one sandbox."""
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        cp = registry.control_plane(rec.region)
        info = await cp.get_microvm(rec.microvm_id)
        return ok({"name": name, "microvm": info.raw})

    @mcp.tool()
    @tool_handler
    async def sandbox_stop(name: str) -> dict:
        """Suspend a sandbox (preserves state; resume with `sandbox_start`)."""
        sandbox = await registry.get(name)
        await sandbox.suspend()
        return ok({"name": name, "state": "suspended"})

    @mcp.tool()
    @tool_handler
    async def sandbox_remove(name: str) -> dict:
        """Terminate a sandbox and remove it from local state."""
        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        sandbox = await Sandbox.attach(
            name,
            rec.microvm_id,
            endpoint=rec.endpoint,
            region=rec.region or config.region,
            verify_tls=config.verify_tls,
        )
        await sandbox.terminate()
        registry.store.delete(name)
        registry.forget(name)
        return ok({"name": name, "removed": True})

    @mcp.tool()
    @tool_handler
    async def sandbox_wait(
        name: str, target: str = "running", timeout: float = 120.0
    ) -> dict:
        """Wait until a sandbox reaches a terminal or target state.

        `target` is one of the SandboxState values (e.g. `running`, `suspended`,
        `stopped`). Returns the observed state once reached or on timeout.
        """
        import asyncio
        import time

        rec = registry.store.get(name)
        if rec is None:
            return err(f"no sandbox named {name!r}", code="not_found")
        cp = registry.control_plane(rec.region)
        want = target.lower()
        deadline = time.monotonic() + timeout
        last: str | None = None
        while time.monotonic() < deadline:
            info = await cp.get_microvm(rec.microvm_id)
            state = SandboxState.from_aws_status(info.status)
            last = str(state)
            if last == want or state in {SandboxState.FAILED, SandboxState.STOPPED}:
                return ok({"name": name, "state": last, "reached_target": last == want})
            await asyncio.sleep(1.0)
        return err(
            f"timed out after {timeout}s waiting for {want!r} (last: {last})",
            code="timeout",
            state=last,
        )
