"""Open a persistent hosted computer; lifecycle orchestration belongs to the API."""

import math
import os
import time

import typer

from agent_sandbox.remote import request, vm_path

from .accounts import handled
from .output import details


def present(result, as_json=False):
    if as_json:
        details(result, True)
        return
    view = {
        "name": result["name"],
        "type": (result.get("agent") or {}).get("provider", "Computer"),
        "status": result.get("status", result.get("state", "Unknown")),
        "idle_minutes": result.get("idle_minutes", 5),
        "disposable": result.get("disposable", False),
    }
    if result.get("project"):
        view["project"] = result["project"]
    if result.get("attention"):
        view["attention"] = result["attention"]
    if result.get("startup") and result["startup"].get("error"):
        view["attention"] = result["startup"]["error"]
    details(view, title="Computer")


def register(app):
    @app.command("usage")
    @handled
    def usage(as_json: bool = typer.Option(False, "--json")):
        """Show your plan, pooled machine-hours and saved-machine allowance."""
        from .agent_profiles import api

        result = api("GET", "/v1/billing")
        details(
            result
            if as_json
            else {
                "plan": result["plan"],
                "hours_used": round(result["used_seconds"] / 3600, 2),
                "hours_included": result["hours"],
                "machines": f"{result['machine_count']} / {result['machine_limit']}",
            },
            as_json,
            title="Usage",
        )

    @app.command("rename")
    @handled
    def rename(name: str, label: str, as_json: bool = typer.Option(False, "--json")):
        """Change a display name; the machine's CLI identifier stays the same."""
        from .agent_profiles import api

        label = label.strip()
        if not label or len(label) > 63 or any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise typer.BadParameter(
                "Use a display name of 1–63 characters without control characters."
            )
        details(api("POST", vm_path(name) + "/rename", {"label": label}), as_json)

    @app.command("configure")
    @handled
    def configure(
        name: str,
        idle_minutes: float | None = typer.Option(None, "--idle-minutes", min=0.001, max=15),
        model: str | None = typer.Option(
            None, "--model", help="Machine default model; 'default' resets it."
        ),
        effort: str | None = typer.Option(
            None, "--effort", help="Machine default effort; 'default' resets it."
        ),
        integration: list[str] | None = typer.Option(
            None, "--integration", help="Replace machine integrations; repeat for multiple."
        ),
        no_integrations: bool = typer.Option(
            False, "--no-integrations", help="Clear machine integrations."
        ),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """View or edit machine settings. Power off before changing agent defaults."""
        from .agent_profiles import api, select

        if integration and no_integrations:
            raise typer.BadParameter("Choose --integration or --no-integrations.")
        if idle_minutes is not None and not math.isfinite(idle_minutes):
            raise typer.BadParameter("Idle minutes must be finite.")
        route = vm_path(name) + "/configuration"
        current = api("GET", route)
        agent_change = (
            model is not None or effort is not None or bool(integration) or no_integrations
        )
        if idle_minutes is None and not agent_change:
            details(current, as_json)
            return
        if agent_change and (
            not (current.get("agent") or {}).get("provider") or current["state"] != "TERMINATED"
        ):
            raise typer.BadParameter("Power off an agent machine before changing agent defaults.")
        body = {
            "label": current.get("label") or name,
            "idle_minutes": idle_minutes if idle_minutes is not None else current["idle_minutes"],
        }
        if model is not None:
            body["model"] = model
        if effort is not None:
            body["effort"] = effort
        if integration or no_integrations:
            body["integrations"] = select(
                integration,
                no_integrations=no_integrations,
                unattended=True,
                provider=current["agent"]["provider"],
            )
        details(api("POST", route, body), as_json)

    @app.command("wake")
    @handled
    def wake_computer(
        names: list[str] = typer.Argument(None, help="Computer names."),
        all_computers: bool = typer.Option(False, "--all", help="Select all your computers."),
        parallel: int = typer.Option(4, "--parallel", min=1, max=16),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show targets without changing them."
        ),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Wake one or more saved computers."""
        from .bulk import lifecycle

        lifecycle("wake", names, all_computers, parallel, dry_run, as_json, request, present)

    @app.command("shutdown")
    @handled
    def shutdown(
        names: list[str] = typer.Argument(None, help="Computer names."),
        all_computers: bool = typer.Option(False, "--all", help="Select all your computers."),
        parallel: int = typer.Option(4, "--parallel", min=1, max=16),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show targets without changing them."
        ),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Save and shut down one or more computers."""
        from .bulk import lifecycle

        lifecycle("shutdown", names, all_computers, parallel, dry_run, as_json, request, present)

    @app.command("delete")
    @handled
    def delete(
        names: list[str] = typer.Argument(None, help="Computer names."),
        all_computers: bool = typer.Option(False, "--all", help="Select all your computers."),
        parallel: int = typer.Option(4, "--parallel", min=1, max=16),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show targets without changing them."
        ),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Permanently delete computers, saved workspaces, and chat history."""
        from .bulk import lifecycle

        lifecycle("delete", names, all_computers, parallel, dry_run, as_json, request, present)

    @app.command("wait")
    @handled
    def wait_computer(
        name: str,
        timeout: float = typer.Option(300, "--timeout", min=0.1),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Show startup progress and wait until the computer is ready."""
        from agent_sandbox.errors import SandboxError

        from .main import _fail, console
        from .output import label

        try:
            interval = float(os.getenv("VMNEST_WAIT_POLL_SECONDS", "1"))
            if not math.isfinite(timeout) or not 0 < interval < float("inf"):
                raise SandboxError("Wait timing must be finite and positive")
            deadline = time.monotonic() + timeout
            previous = None
            while True:
                result = request("GET", vm_path(name))
                status = result.get("status", result.get("state"))
                if status != previous and not as_json:
                    console.print(f"{name}: {label(status)}", markup=False)
                    previous = status
                if status in ("ready", "RUNNING"):
                    if as_json:
                        present(result, True)
                    return
                if status in ("failed", "unavailable", "ended"):
                    error = (result.get("startup") or {}).get("error") or result.get("attention")
                    raise SandboxError(
                        error or "Computer could not become ready; inspect it for details"
                    )
                if status == "sleeping":
                    raise SandboxError(f"Computer is sleeping; use vmnest wake {name}")
                if time.monotonic() >= deadline:
                    raise SandboxError("Timed out waiting; startup continues on the server")
                time.sleep(interval)
        except SandboxError as exc:
            _fail(str(exc))
