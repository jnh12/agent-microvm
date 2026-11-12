"""Hosted checkpoint commands; persistence belongs to the service."""

import json
import time
import uuid
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountError

from .accounts import handled
from .agent_profiles import api
from .agent_runs import poll_interval


def display(result, wait):
    typer.echo(json.dumps(result))
    while wait and result["phase"] not in ("completed", "failed"):
        time.sleep(poll_interval())
        updated = api("GET", "/v1/agent-state-operations/" + quote(result["id"], safe=""))
        if updated != result:
            typer.echo(json.dumps(updated))
        result = updated
    if result["phase"] == "failed":
        raise typer.Exit(1)


def submit(name, action, request_key, terminate, wait, agent_prefix=True):
    key = request_key or str(uuid.uuid4())
    try:
        result = api(
            "POST",
            "/v1/vms/" + quote(name, safe="") + ("/agent/" if agent_prefix else "/") + action,
            {"request_key": key, "terminate": terminate},
        )
    except AccountError as exc:
        raise AccountError(
            exc.code, str(exc) + " Request key: " + key + ". Reuse it when retrying."
        ) from None
    display({**result, "request_key": key}, wait)


def register(app, agent_prefix=True):
    def require_hosted():
        from agent_sandbox.remote import remote_url

        if not agent_prefix and not remote_url():
            raise AccountError("HOSTED_REQUIRED", "Workspace checkpoints require hosted mode.")

    @app.command("checkpoint")
    @handled
    def checkpoint(
        name: str,
        terminate: bool = typer.Option(False, "--terminate"),
        wait: bool = typer.Option(False, "--wait"),
        request_key: str | None = typer.Option(None, "--request-key"),
    ):
        """Save workspace and native conversations; optionally terminate after verification."""
        require_hosted()
        submit(name, "checkpoints", request_key, terminate, wait, agent_prefix)

    @app.command("restore")
    @handled
    def restore(
        name: str,
        wait: bool = typer.Option(False, "--wait"),
        request_key: str | None = typer.Option(None, "--request-key"),
    ):
        """Restore the latest checkpoint into a fresh VM under the same name."""
        require_hosted()
        submit(name, "restore", request_key, False, wait, agent_prefix)

    @app.command("workspaces")
    @handled
    def workspaces():
        """List saved workspaces, including those without a running VM."""
        require_hosted()
        typer.echo(json.dumps(api("GET", "/v1/agent-workspaces")))

    @app.command("checkpoint-status")
    @handled
    def status(operation: str, wait: bool = typer.Option(False, "--wait")):
        """Inspect a checkpoint or restore operation."""
        require_hosted()
        display(api("GET", "/v1/agent-state-operations/" + quote(operation, safe="")), wait)
