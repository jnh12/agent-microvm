"""Hosted project setup status and explicit retry."""

import json
import uuid
from urllib.parse import quote

import typer

from .accounts import handled
from .agent_profiles import api

app = typer.Typer(help="Inspect and retry saved project setup.")


@app.command("retry")
@handled
def retry(name: str, request_key: str | None = typer.Option(None, "--request-key")):
    key = request_key or str(uuid.uuid4())
    typer.echo(
        json.dumps(
            api("POST", "/v1/vms/" + quote(name, safe="") + "/project/retry", {"request_key": key})
        )
    )


@app.command("logs")
@handled
def logs(name: str):
    result = api("GET", "/v1/vms/" + quote(name, safe="") + "/project/logs")
    typer.echo(result["output"])
    if result["truncated"]:
        typer.echo("[Showing the end of the setup log]")
