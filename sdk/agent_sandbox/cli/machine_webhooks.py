"""Inbound webhooks for existing computers."""

import json
import os

import typer

from agent_sandbox.accounts import AccountClient, AccountError, service_url

from .accounts import handled
from .agent_profiles import api
from .output import details
from .webhooks import validate_request_key

app = typer.Typer(
    name="machine-webhook", help="Manage webhooks for existing computers.", no_args_is_help=True
)
ROOT = "/v1/machine-webhooks"


def resolve(value):
    items = api("GET", ROOT)["items"]
    for field in ("id", "name"):
        for item in items:
            if item[field] == value:
                return item
    raise AccountError("WEBHOOK_NOT_FOUND", "Machine webhook not found.")


@app.command("create")
@handled
def create(
    name: str,
    computer: str = typer.Option(..., "--computer"),
    action: list[str] = typer.Option(
        None, "--action", help="Repeat: wake, prompt, shutdown. Default: wake and prompt."
    ),
    as_json: bool = typer.Option(False, "--json"),
):
    actions = action or ["wake", "prompt"]
    if any(a not in ("wake", "prompt", "shutdown") for a in actions):
        raise AccountError("INVALID_REQUEST", "Choose wake, prompt, or shutdown.")
    r = api("POST", ROOT, {"name": name, "computer": computer, "actions": actions})
    r["trigger_url"] = service_url().rstrip("/") + r["trigger_path"]
    details(r, as_json)


@app.command("list")
@handled
def listing(as_json: bool = typer.Option(False, "--json")):
    details(api("GET", ROOT), as_json)


@app.command("deliveries")
@handled
def deliveries(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("GET", ROOT + "/" + resolve(identifier)["id"] + "/deliveries"), as_json)


@app.command("rotate")
@handled
def rotate(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("POST", ROOT + "/" + resolve(identifier)["id"] + "/rotate"), as_json)


@app.command("revoke")
@handled
def revoke(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("DELETE", ROOT + "/" + resolve(identifier)["id"]), as_json)


@app.command("trigger")
@handled
def trigger(
    identifier: str,
    action: str = typer.Option("prompt", "--action"),
    prompt: str = typer.Option("", "--prompt"),
    input_json: str = typer.Option("{}", "--input-json"),
    session: str = typer.Option("main", "--session"),
    request_key: str = typer.Option(..., "--request-key"),
    secret_env: str = typer.Option("VMNEST_WEBHOOK_SECRET", "--secret-env"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Send a real delivery. Read the webhook secret from an environment variable."""
    validate_request_key(request_key)
    token = os.getenv(secret_env, "")
    if not token:
        raise AccountError("INVALID_REQUEST", "Set the webhook secret environment variable first.")
    try:
        data = json.loads(input_json)
    except ValueError:
        raise AccountError("INVALID_REQUEST", "Input must be a JSON object.") from None
    if not isinstance(data, dict):
        raise AccountError("INVALID_REQUEST", "Input must be a JSON object.")
    row = resolve(identifier)
    r = AccountClient(service_url()).request(
        "POST",
        ROOT + "/" + row["id"] + "/trigger",
        token=token,
        request_key=request_key,
        body={"action": action, "prompt": prompt, "input": data, "session": session},
    )
    details(r, as_json)


@app.command("delete")
@handled
def delete(identifier: str, as_json: bool = typer.Option(False, "--json")):
    """Remove a webhook and revoke access. Already-running work can finish."""
    details(api("DELETE", ROOT + "/" + resolve(identifier)["id"] + "?permanent=true"), as_json)
