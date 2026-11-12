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
        "idle_minutes": result.get("idle_minutes", 15),
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
    @app.command("open")
    @handled
    def open_computer(name: str, as_json: bool = typer.Option(False, "--json")):
        """Return to your computer. Wake it automatically when needed."""
        from agent_sandbox.errors import SandboxError

        from .main import _fail

        try:
            present(request("POST", vm_path(name) + "/open"), as_json)
        except SandboxError as exc:
            _fail(str(exc))

    @app.command("shutdown")
    @handled
    def shutdown(name: str, as_json: bool = typer.Option(False, "--json")):
        """Save and shut down a computer. Open it later to resume."""
        from agent_sandbox.errors import SandboxError
        from .main import _fail

        try:
            present(request("POST", vm_path(name) + "/shutdown"), as_json)
        except SandboxError as exc:
            _fail(str(exc))

    @app.command("delete")
    @handled
    def delete(name: str, as_json: bool = typer.Option(False, "--json")):
        """Permanently delete a computer, its saved workspace, and chat history."""
        from agent_sandbox.errors import SandboxError
        from .main import _fail

        try:
            result = request("DELETE", vm_path(name) + "?permanent=true")
            if as_json:
                details(result, True)
            else:
                typer.echo(f"{name}: {result['status']}")
        except SandboxError as exc:
            _fail(str(exc))

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
                    raise SandboxError(f"Computer is sleeping; use vmnest open {name}")
                if time.monotonic() >= deadline:
                    raise SandboxError("Timed out waiting; startup continues on the server")
                time.sleep(interval)
        except SandboxError as exc:
            _fail(str(exc))
