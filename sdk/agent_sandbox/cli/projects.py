"""Hosted project setup status and explicit retry."""

import uuid
from urllib.parse import quote

import typer

from .accounts import handled
from .agent_profiles import api
from .output import details

app = typer.Typer(help="Inspect and retry saved project setup.")


@app.command("retry")
@handled
def retry(
    name: str,
    request_key: str | None = typer.Option(None, "--request-key"),
    as_json: bool = typer.Option(False, "--json"),
):
    key = request_key or str(uuid.uuid4())
    result = api("POST", "/v1/vms/" + quote(name, safe="") + "/project/retry", {"request_key": key})
    details(result, as_json, "Project setup")


@app.command("logs")
@handled
def logs(name: str):
    result = api("GET", "/v1/vms/" + quote(name, safe="") + "/project/logs")
    typer.echo(result["output"])
    if result["truncated"]:
        typer.echo("[Showing the end of the setup log]")
