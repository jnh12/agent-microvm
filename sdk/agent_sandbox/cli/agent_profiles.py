"""Thin hosted API commands for agent integration defaults and profiles."""

import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountClient, AccountError, service_url
from agent_sandbox.cli.accounts import handled

app = typer.Typer(name="profile", help="Manage account-owned agent integration profiles.")


def workflow(instructions, instructions_file, setup_command, timeout):
    if instructions is not None and instructions_file is not None:
        raise AccountError("INVALID_REQUEST", "Choose --instructions or --instructions-file.")
    if instructions_file is not None:
        try:
            instructions = Path(instructions_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise AccountError("INVALID_REQUEST", "Could not read the instructions file.") from None
    return {
        key: value
        for key, value in {
            "instructions": instructions,
            "setup_command": setup_command,
            "timeout_seconds": timeout,
        }.items()
        if value is not None
    }


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
    from .agent_models import interactive_terminal

    unattended = unattended or not interactive_terminal()
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
            ["Name", "Provider", "Model", "Effort", "Connections"],
            (
                (
                    item["name"],
                    item.get("provider", "-"),
                    item.get("model") or "Provider default",
                    item.get("effort") or "Default",
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
    name: str | None = typer.Argument(None),
    instructions: str | None = typer.Option(None, "--instructions"),
    instructions_file: str | None = typer.Option(None, "--instructions-file"),
    setup_command: str | None = typer.Option(None, "--setup"),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        min=60,
        max=3600,
        help="Webhook lifetime in seconds, including queue and setup.",
    ),
    effort: str | None = typer.Option(None, "--effort"),
    model: str | None = typer.Option(None, "--model"),
    provider: Literal["codex", "claude"] = typer.Option("codex", "--provider"),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    from .agent_models import interactive_terminal

    if name is None:
        if as_json or not interactive_terminal():
            raise AccountError(
                "NAME_REQUIRED",
                "Supply a profile name, for example: vmnest agent profile create support.",
            )
        name = typer.prompt("Profile name").strip()
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,62}", name):
        raise AccountError(
            "INVALID_REQUEST",
            "Use a profile name starting with a letter, followed by letters, numbers, "
            "hyphens or underscores (max 63 characters).",
        )
    settings = workflow(instructions, instructions_file, setup_command, timeout)
    selected = select(integration, all_integrations, no_integrations, as_json, provider)
    from .agent_models import choose

    model, effort = choose(provider, model, effort, as_json=as_json)
    emit(
        api(
            "POST",
            "/v1/agent-profiles",
            {
                "name": name,
                "provider": provider,
                "integrations": selected,
                **settings,
                **({"model": model} if model is not None else {}),
                **({"effort": effort} if effort is not None else {}),
            },
        ),
        as_json,
    )


@app.command("update")
@handled
def update(
    name: str,
    instructions: str | None = typer.Option(None, "--instructions"),
    instructions_file: str | None = typer.Option(None, "--instructions-file"),
    setup_command: str | None = typer.Option(None, "--setup", help="Empty string clears setup."),
    timeout: int | None = typer.Option(None, "--timeout", min=60, max=3600),
    effort: str | None = typer.Option(None, "--effort"),
    model: str | None = typer.Option(
        None, "--model", help="Set model; default resets to provider default."
    ),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    settings = workflow(instructions, instructions_file, setup_command, timeout)
    saved = api("GET", "/v1/agent-profiles/" + quote(name, safe=""))
    provider = saved["provider"]
    from .agent_models import choose

    model, effort = choose(
        provider, model, effort, as_json=as_json, current_model=saved.get("model")
    )
    body = settings
    if model is not None:
        body["model"] = model
    if effort is not None:
        body["effort"] = effort
    if (not body) or integration or all_integrations or no_integrations:
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


@app.command("inspect")
@handled
def inspect(name: str, as_json: bool = typer.Option(False, "--json")):
    """Show all saved profile settings, including webhook instructions."""
    from .output import details

    details(api("GET", "/v1/agent-profiles/" + quote(name, safe="")), as_json)


@app.command("delete")
@handled
def delete(name: str, as_json: bool = typer.Option(False, "--json")):
    emit(api("DELETE", "/v1/agent-profiles/" + quote(name, safe="")), as_json)
