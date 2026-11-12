"""Open a persistent hosted computer; lifecycle orchestration belongs to the API."""

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
