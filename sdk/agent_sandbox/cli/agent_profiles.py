"""Thin hosted API commands for agent integration defaults and profiles."""

import json
import os
import re
from typing import Literal
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountClient, AccountError, service_url
from agent_sandbox.cli.accounts import handled

app = typer.Typer(name="profile", help="Manage account-owned agent integration profiles.")


def api(method, path, body=None):
    timeout = float(os.environ.get("VMNEST_DISCOVERY_TIMEOUT_SECONDS", "150"))
    if not 0 < timeout < float("inf"):
        raise AccountError("INVALID_REQUEST", "Discovery timeout must be finite and positive.")
    return AccountClient(service_url()).authenticated(method, path, body=body, timeout=timeout)


def select(
    integrations=None,
    all_integrations=False,
    no_integrations=False,
    unattended=False,
    provider="codex",
):
    if sum((bool(integrations), all_integrations, no_integrations)) > 1:
        raise AccountError(
            "INVALID_REQUEST", "Choose --integration, --all-integrations, or --no-integrations."
        )
    if no_integrations:
        return []
    if unattended and not (integrations or all_integrations):
        raise AccountError(
            "SELECTION_REQUIRED", "Specify --integration, --all-integrations, or --no-integrations."
        )
    items = api("GET", f"/v1/agent-connections/{provider}/integrations")["items"]
    available = [item for item in items if item["available"]]
    if all_integrations:
        return [item["id"] for item in available]
    if integrations is None:
        for index, item in enumerate(available, 1):
            typer.echo(f"{index}. {item['name']}")
        if not available:
            typer.echo("No available account integrations; saving an empty selection.")
            return []
        answer = typer.prompt("Enable integrations by name or number, comma-separated; or 'none'")
        if answer.strip().lower() == "none":
            return []
        integrations = [part.strip() for part in answer.split(",")]

    def slug(name):
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    selected = []
    for value in integrations:
        matches = [
            item["id"]
            for index, item in enumerate(available, 1)
            if value == item["id"] or value == str(index) or slug(value) == slug(item["name"])
        ]
        if len(matches) != 1:
            raise AccountError(
                "INTEGRATION_UNAVAILABLE",
                f"Integration '{value}' is unavailable or ambiguous; use its exact ID.",
            )
        selected.append(matches[0])
    return sorted(set(selected))


def emit(result, as_json):
    if as_json:
        typer.echo(json.dumps(result))
    else:
        items = result.get("items", [result])
        for item in items:
            typer.echo(
                f"{item['name']}: "
                + (", ".join(item.get("integrations", [])) or item.get("status", "no integrations"))
            )
        if not items:
            typer.echo("No saved profiles.")


@app.command("create")
@handled
def create(
    name: str,
    provider: Literal["codex", "claude"] = typer.Option("codex", "--provider"),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    selected = select(integration, all_integrations, no_integrations, as_json, provider)
    emit(
        api(
            "POST",
            "/v1/agent-profiles",
            {"name": name, "provider": provider, "integrations": selected},
        ),
        as_json,
    )


@app.command("update")
@handled
def update(
    name: str,
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    provider = api("GET", "/v1/agent-profiles/" + quote(name, safe=""))["provider"]
    selected = select(integration, all_integrations, no_integrations, as_json, provider)
    emit(
        api("PATCH", "/v1/agent-profiles/" + quote(name, safe=""), {"integrations": selected}),
        as_json,
    )


@app.command("list")
@handled
def listing(as_json: bool = typer.Option(False, "--json")):
    emit(api("GET", "/v1/agent-profiles"), as_json)


@app.command("delete")
@handled
def delete(name: str, as_json: bool = typer.Option(False, "--json")):
    emit(api("DELETE", "/v1/agent-profiles/" + quote(name, safe="")), as_json)
