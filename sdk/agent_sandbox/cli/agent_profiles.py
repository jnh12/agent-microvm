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
    setting, default = (
        ("VMNEST_COMPUTER_TIMEOUT_SECONDS", "240")
        if method == "POST" and path.startswith("/v1/vms/")
        else ("VMNEST_DISCOVERY_TIMEOUT_SECONDS", "150")
    )
    timeout = float(os.environ.get(setting, default))
    if not 0 < timeout < float("inf"):
        raise AccountError("INVALID_REQUEST", "Request timeout must be finite and positive.")
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
    if all_integrations:
        return "all"
    items = api("GET", f"/v1/agent-connections/{provider}/integrations")["items"]
    available = [item for item in items if item["available"]]
    if integrations is None:
        for index, item in enumerate(available, 1):
            typer.echo(f"{index}. {item['name']}")
        if not available:
            typer.echo("No available integrations yet; choose all for future connections or none.")
        answer = typer.prompt(
            "Enable integrations by name or number, comma-separated; or 'all' / 'none'"
        )
        if answer.strip().lower() == "all":
            return "all"
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
        from .output import table

        items = result.get("items", [result])
        table(
            "Agent profiles",
            ["Name", "Provider", "Model", "Connections"],
            (
                (
                    item["name"],
                    item.get("provider", "-"),
                    item.get("model") or "Provider default",
                    "All, including future connections"
                    if item.get("integrations") == "all"
                    else ", ".join(item.get("integrations", [])) or item.get("status", "None"),
                )
                for item in items
            ),
            empty="No saved profiles.",
        )


@app.command("create")
@handled
def create(
    name: str,
    model: str | None = typer.Option(None, "--model"),
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
            {
                "name": name,
                "provider": provider,
                "integrations": selected,
                **({"model": model} if model is not None else {}),
            },
        ),
        as_json,
    )


@app.command("update")
@handled
def update(
    name: str,
    model: str | None = typer.Option(
        None, "--model", help="Set model; default resets to provider default."
    ),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    provider = api("GET", "/v1/agent-profiles/" + quote(name, safe=""))["provider"]
    body = {}
    if model is not None:
        body["model"] = model
    if model is None or integration or all_integrations or no_integrations:
        body["integrations"] = select(
            integration, all_integrations, no_integrations, as_json, provider
        )
    emit(
        api("PATCH", "/v1/agent-profiles/" + quote(name, safe=""), body),
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
