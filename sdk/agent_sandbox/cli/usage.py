"""Hosted usage presentation; measurements belong to the service."""

from urllib.parse import urlencode

import typer

from agent_sandbox.errors import SandboxError
from agent_sandbox.remote import remote_url, request


def register(app):
    @app.command()
    def usage(
        start: str | None = typer.Option(None, "--from", help="ISO 8601 date/time with timezone."),
        end: str | None = typer.Option(None, "--to", help="Exclusive ISO 8601 end with timezone."),
        as_json: bool = typer.Option(False, "--json", help="Print the usage API response as JSON."),
    ):
        """Show hosted account usage; defaults to the current UTC month."""
        from rich.table import Table

        from .main import _fail, console

        try:
            if not remote_url():
                raise SandboxError(
                    "Usage reporting requires hosted mode; select it with vmnest setup"
                )
            query = urlencode({k: v for k, v in (("from", start), ("to", end)) if v is not None})
            result = request("GET", "/v1/usage" + ("?" + query if query else ""))
            if as_json:
                console.print_json(data=result)
                return
            console.print(f"Usage: {result['period']['from']} to {result['period']['to']}")
            console.print(f"Tracking started: {result['tracking_started_at']}")
            current = result["current"]
            console.print(
                f"Current: {current['running']} running, {current['suspended']} suspended, "
                f"{current['unknown']} unknown, {current['other']} other"
            )
            console.print(f"VMs created in period: {result['creations']}")
            table = Table("VM", "ID", "RUNNING (s)", "SUSPENDED (s)", "UNKNOWN (s)")
            for vm in result["vms"]:
                table.add_row(
                    vm["name"],
                    vm["id"],
                    *(
                        f"{vm[k]:.1f}"
                        for k in ("running_seconds", "suspended_seconds", "unknown_seconds")
                    ),
                )
            table.add_row(
                "TOTAL",
                "",
                *(
                    f"{result['totals'][k]:.1f}"
                    for k in ("running_seconds", "suspended_seconds", "unknown_seconds")
                ),
            )
            console.print(table)
            console.print(
                "Observed-state estimates, not billing measurements. "
                "History before tracking is unavailable."
            )
            if result.get("refresh_failed_vm_ids"):
                console.print("Some VM states could not be refreshed; see --json for details.")
        except SandboxError as exc:
            _fail(str(exc))
