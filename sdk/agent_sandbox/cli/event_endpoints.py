"""Outbound orchestrator callbacks and computer subscriptions."""

from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountError

from .accounts import handled
from .agent_profiles import api
from .output import details

app = typer.Typer(
    name="endpoint", help="Manage outbound event endpoints for orchestrators.", no_args_is_help=True
)
ROOT = "/v1/event-endpoints"


def resolve(identifier):
    items = api("GET", ROOT)["items"]
    for field in ("id", "name"):
        for item in items:
            if item[field] == identifier:
                return item
    raise AccountError("ENDPOINT_NOT_FOUND", "Event endpoint not found.")


@app.command("list")
@handled
def listing(as_json: bool = typer.Option(False, "--json")):
    details(api("GET", ROOT), as_json)


@app.command("create")
@handled
def create(
    name: str,
    url: str = typer.Option(..., "--url"),
    event: list[str] | None = typer.Option(
        None, "--event", help="Repeat to filter event types. Omit for all."
    ),
    as_json: bool = typer.Option(False, "--json"),
):
    """Create a public HTTPS callback; signing secret is returned only once."""
    body = {"name": name, "url": url}
    if event is not None:
        body["events"] = event
    details(api("POST", ROOT, body), as_json)


@app.command("update")
@handled
def update(
    identifier: str,
    url: str | None = typer.Option(None, "--url"),
    name: str | None = typer.Option(None, "--name"),
    event: list[str] | None = typer.Option(None, "--event"),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
    as_json: bool = typer.Option(False, "--json"),
):
    row = resolve(identifier)
    body = {k: row[k] for k in ("name", "url", "events", "enabled")}
    for k, v in dict(name=name, url=url, events=event, enabled=enabled).items():
        if v is not None:
            body[k] = v
    details(api("PUT", ROOT + "/" + row["id"], body), as_json)


@app.command("bind")
@handled
def bind(
    computer: str,
    endpoint: list[str] | None = typer.Option(
        None, "--endpoint", help="Endpoint name or ID. Repeat for multiple."
    ),
    clear: bool = typer.Option(False, "--clear"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Replace a computer's subscriptions immediately, without restarting."""
    if bool(endpoint) == clear:
        raise AccountError("INVALID_REQUEST", "Choose --endpoint or --clear.")
    ids = [resolve(i)["id"] for i in endpoint or []]
    details(
        api("PUT", "/v1/vms/" + quote(computer, safe="") + "/event-endpoints", {"endpoints": ids}),
        as_json,
    )


@app.command("bindings")
@handled
def bindings(computer: str, as_json: bool = typer.Option(False, "--json")):
    details(api("GET", "/v1/vms/" + quote(computer, safe="") + "/event-endpoints"), as_json)


@app.command("test")
@handled
def test(identifier: str, as_json: bool = typer.Option(False, "--json")):
    """Send a signed sample event. Does not start a computer."""
    details(api("POST", ROOT + "/" + resolve(identifier)["id"] + "/test"), as_json)


@app.command("deliveries")
@handled
def deliveries(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("GET", ROOT + "/" + resolve(identifier)["id"] + "/deliveries"), as_json)


@app.command("rotate")
@handled
def rotate(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("POST", ROOT + "/" + resolve(identifier)["id"] + "/rotate"), as_json)


@app.command("delete")
@handled
def delete(identifier: str, as_json: bool = typer.Option(False, "--json")):
    details(api("DELETE", ROOT + "/" + resolve(identifier)["id"]), as_json)
