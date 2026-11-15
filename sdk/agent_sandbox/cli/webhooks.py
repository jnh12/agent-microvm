"""Manage profile-backed disposable webhook jobs through the hosted API."""

from urllib.parse import quote

import typer

from agent_sandbox.accounts import service_url

from .accounts import handled
from .agent_profiles import api
from .output import details, table

app = typer.Typer(
    name="webhook", help="Manage webhooks that run disposable computers.", no_args_is_help=True
)


def path(identifier, suffix="", *, run=False):
    return ("/v1/webhook-runs/" if run else "/v1/webhooks/") + quote(identifier, safe="") + suffix


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
    """Disable new deliveries and cancel queued jobs. Active jobs finish."""
    details(api("DELETE", path(identifier)), as_json)


@app.command("rotate")
@handled
def rotate(identifier: str, as_json: bool = typer.Option(False, "--json")):
    """Replace the trigger secret immediately; return the new secret once."""
    details(api("POST", path(identifier, "/rotate")), as_json)


@app.command("runs")
@handled
def runs(
    identifier: str,
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
):
    """List recent jobs for a webhook."""
    result = api("GET", path(identifier, f"/runs?limit={limit}"))
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
