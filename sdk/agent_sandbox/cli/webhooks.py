"""Manage profile-backed disposable webhook jobs through the hosted API."""

from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountError, service_url

from .accounts import handled
from .agent_profiles import api
from .output import details, table

app = typer.Typer(
    name="webhook", help="Manage webhooks that run disposable computers.", no_args_is_help=True
)


def path(identifier, suffix="", *, run=False):
    return ("/v1/webhook-runs/" if run else "/v1/webhooks/") + quote(identifier, safe="") + suffix


def resolve_hook(identifier):
    """Resolve an account-owned webhook by ID first, then exact name."""
    items = api("GET", "/v1/webhooks")["items"]
    for field in ("id", "name"):
        for item in items:
            if item[field] == identifier:
                return item
    raise AccountError("WEBHOOK_NOT_FOUND", "Webhook not found. Use vmnest webhook list.")


@app.command("create")
@handled
def create(
    name: str,
    profile: str = typer.Option(..., "--profile"),
    concurrency: int = typer.Option(1, "--concurrency", min=1, max=5),
    as_json: bool = typer.Option(False, "--json"),
):
    """Create a trigger URL and secret. The secret is returned only once."""
    result = api(
        "POST", "/v1/webhooks", {"name": name, "profile": profile, "concurrency": concurrency}
    )
    result["trigger_url"] = service_url().rstrip("/") + result["trigger_path"]
    details(result, as_json)


@app.command("list")
@handled
def listing(as_json: bool = typer.Option(False, "--json")):
    """List your webhook definitions, without secrets."""
    result = api("GET", "/v1/webhooks")
    if as_json:
        details(result, True)
    else:
        table(
            "Webhooks",
            ["ID", "Name", "Profile", "Enabled", "Concurrency"],
            (
                (r["id"], r["name"], r["profile"], bool(r["enabled"]), r["concurrency"])
                for r in result["items"]
            ),
        )


@app.command("revoke")
@handled
def revoke(identifier: str, as_json: bool = typer.Option(False, "--json")):
    """Revoke a webhook by name or ID. Active jobs finish."""
    details(api("DELETE", path(resolve_hook(identifier)["id"])), as_json)


@app.command("rotate")
@handled
def rotate(identifier: str, as_json: bool = typer.Option(False, "--json")):
    """Rotate a webhook by name or ID; return the new secret once."""
    hook = resolve_hook(identifier)
    if not hook["enabled"]:
        raise AccountError(
            "WEBHOOK_REVOKED",
            "This webhook is revoked. Rotation cannot enable it; create a new webhook.",
        )
    details(api("POST", path(hook["id"], "/rotate")), as_json)


@app.command("runs")
@handled
def runs(
    identifier: str,
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
):
    """List recent jobs for a webhook name or ID."""
    result = api("GET", path(resolve_hook(identifier)["id"], f"/runs?limit={limit}"))
    if as_json:
        details(result, True)
    else:
        table(
            "Webhook runs",
            ["ID", "Computer", "Status", "Error"],
            ((r["id"], r["computer"], r["status"], r["error"]) for r in result["items"]),
        )


@app.command("inspect")
@handled
def inspect(run_id: str, as_json: bool = typer.Option(False, "--json")):
    """Inspect a job's progress and cleanup status."""
    details(api("GET", path(run_id, run=True)), as_json)


@app.command("logs")
@handled
def logs(
    run_id: str,
    after: int = typer.Option(0, "--after", min=0),
    as_json: bool = typer.Option(False, "--json"),
):
    """Read retained agent events; use the returned cursor for the next page."""
    details(api("GET", path(run_id, f"/events?after={after}", run=True)), as_json)


@app.command("cancel")
@handled
def cancel(run_id: str, as_json: bool = typer.Option(False, "--json")):
    """Cancel a job and request termination of its disposable computer."""
    details(api("POST", path(run_id, "/cancel", run=True)), as_json)


@app.command("update")
@handled
def update(
    identifier: str,
    name: str | None = typer.Option(None, "--name"),
    profile: str | None = typer.Option(None, "--profile"),
    concurrency: int | None = typer.Option(None, "--concurrency", min=1, max=5),
    as_json: bool = typer.Option(False, "--json"),
):
    """Change future deliveries. Accepted jobs retain their original profile."""
    body = {
        k: v
        for k, v in dict(name=name, profile=profile, concurrency=concurrency).items()
        if v is not None
    }
    if not body:
        raise AccountError("INVALID_REQUEST", "Supply --name, --profile or --concurrency.")
    details(api("PATCH", path(resolve_hook(identifier)["id"]), body), as_json)


@app.command("test")
@handled
def test_delivery(
    identifier: str,
    secret_file: str = typer.Option(..., "--secret-file", help="Secret file, or - for stdin."),
    request_key: str = typer.Option(
        ..., "--request-key", help="Reuse this key to safely retry a delivery."
    ),
    prompt: str = typer.Option("", "--prompt"),
    input_file: str | None = typer.Option(None, "--input-file", help="JSON object file."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Send a real delivery. Starts a disposable computer and uses normal quota."""
    import json

    from agent_sandbox.accounts import AccountClient

    from .cloud_defaults import read_input

    if input_file == "-" and secret_file == "-":
        raise AccountError("INVALID_REQUEST", "Only one input can read stdin.")
    try:
        value = json.loads(read_input(input_file, 65536)) if input_file else {}
        if not isinstance(value, dict):
            raise ValueError()
    except ValueError:
        raise AccountError("INVALID_REQUEST", "Input must be a JSON object.") from None
    hook = resolve_hook(identifier)
    token = read_input(secret_file, 256).strip()
    result = AccountClient(service_url()).request(
        "POST",
        path(hook["id"], "/trigger"),
        token=token,
        body={"prompt": prompt, "input": value},
        request_key=request_key,
    )
    details(result, as_json)
