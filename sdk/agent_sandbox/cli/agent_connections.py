"""Connect existing local subscription logins to a VMNest account."""

import json
import os
from pathlib import Path
from typing import Literal

import typer

from agent_sandbox.accounts import AccountClient, AccountError, service_url
from agent_sandbox.cli.accounts import handled
from agent_sandbox.cli.agent_profiles import api, select
from agent_sandbox.cli.agent_profiles import app as profiles_app

app = typer.Typer(name="agent", help="Manage saved agent account connections.")
app.add_typer(profiles_app, name="profile")
Provider = Literal["codex", "claude"]


def credential_path(provider):
    if provider == "codex":
        return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    return (
        Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        / ".credentials.json"
    )


def clean_credentials(provider, value):
    try:
        if not isinstance(value, dict):
            raise ValueError()
        field = "tokens" if provider == "codex" else "claudeAiOauth"
        keys = (
            ("access_token", "refresh_token", "id_token", "account_id")
            if provider == "codex"
            else (
                "accessToken",
                "refreshToken",
                "expiresAt",
                "scopes",
                "subscriptionType",
                "rateLimitTier",
            )
        )
        tokens = value.get(field)
        required = keys[:3] if provider == "codex" else keys[:2]
        if not isinstance(tokens, dict) or not all(
            isinstance(tokens.get(k), str) and tokens[k] for k in required
        ):
            raise ValueError()
        if provider == "codex" and value.get("OPENAI_API_KEY"):
            raise ValueError()
        result = {field: {k: tokens[k] for k in keys if k in tokens}}
        if provider == "codex" and isinstance(value.get("last_refresh"), str):
            result["last_refresh"] = value["last_refresh"]
        return result
    except (ValueError, TypeError):
        raise AccountError(
            "LOCAL_AUTH_UNAVAILABLE", "No supported subscription login found."
        ) from None


def read_credentials(provider, path):
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        return clean_credentials(provider, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        raise AccountError(
            "LOCAL_AUTH_UNAVAILABLE", "No supported subscription login found."
        ) from None


@app.command()
@handled
def connect(
    provider: Provider | None = typer.Option(None, "--provider"),
    yes: bool = typer.Option(
        False, "--yes", help="Explicitly consent to uploading selected credentials."
    ),
    replace: bool = typer.Option(False, "--replace"),
    credentials_file: Path | None = typer.Option(None, "--credentials-file"),
    as_json: bool = typer.Option(False, "--json"),
    source: str | None = typer.Option(None, "--source", help="local, keychain, or wsl:DISTRO"),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
):
    from .agent_discovery import discover

    url = service_url()
    if provider is None:
        if yes or as_json or credentials_file:
            raise AccountError(
                "PROVIDER_REQUIRED", "Specify --provider codex or --provider claude."
            )
        candidates = []
        for name in ("codex", "claude"):
            found = discover(name, clean_credentials)
            typer.echo(f"{name}: " + (", ".join(found) or "no supported login found"))
            if found:
                candidates.append(name)
        if not candidates:
            raise AccountError("LOCAL_AUTH_UNAVAILABLE", "No supported local agent login found.")
        selected = typer.prompt(
            "Connect providers, comma-separated (" + ", ".join(candidates) + ")"
        )
        names = list(dict.fromkeys(name.strip() for name in selected.split(",")))
        if any(name not in candidates for name in names):
            raise AccountError("INVALID_PROVIDER", "Select detected providers.")
        for name in names:
            connect(
                provider=name,
                yes=False,
                replace=replace,
                credentials_file=None,
                as_json=False,
                source=source,
                integration=integration,
                all_integrations=all_integrations,
                no_integrations=no_integrations,
            )
        return
    if sum((bool(integration), all_integrations, no_integrations)) > 1:
        raise AccountError("INVALID_REQUEST", "Choose one integration selection mode.")
    if (yes or as_json) and not (integration or all_integrations or no_integrations):
        raise AccountError(
            "SELECTION_REQUIRED", "Specify --integration, --all-integrations, or --no-integrations."
        )
    if credentials_file:
        if source:
            raise AccountError("INVALID_SOURCE", "Use --source or --credentials-file, not both.")
        payload = read_credentials(provider, credentials_file)
    else:
        found = discover(provider, clean_credentials)
        if source is None and len(found) == 1:
            source = next(iter(found))
        if source is None and found and not (yes or as_json):
            source = typer.prompt("Choose login source (" + ", ".join(found) + ")")
        if source not in found:
            raise AccountError(
                "LOCAL_AUTH_UNAVAILABLE",
                "Choose an available --source: " + (", ".join(found) or "none"),
            )
        payload = found[source]
    if not yes:
        if as_json:
            raise AccountError("CONSENT_REQUIRED", "Use --yes to authorize credential upload.")
        typer.confirm(
            f"Upload your {provider} subscription credentials to {url} for future cloud VMs?",
            abort=True,
        )
    result = AccountClient(url).authenticated(
        "PUT",
        f"/v1/agent-connections/{provider}",
        body={"credentials": payload, "replace": replace},
    )
    if provider in ("codex", "claude"):
        try:
            chosen = select(
                integration, all_integrations, no_integrations, yes or as_json, provider
            )
            configured = api("PATCH", f"/v1/agent-connections/{provider}", {"integrations": chosen})
            result["integrations"] = configured["integrations"]
        except AccountError as exc:
            raise AccountError(
                exc.code,
                "Credentials were saved, but integration defaults were not updated. Use vmnest agent configure. "
                + str(exc),
            ) from None
    typer.echo(
        json.dumps(result)
        if as_json
        else f"{provider}: saved to your VMNest account (not yet verified with the provider)."
    )


@app.command()
@handled
def status(as_json: bool = typer.Option(False, "--json")):
    result = AccountClient(service_url()).authenticated("GET", "/v1/agent-connections")
    if as_json:
        typer.echo(json.dumps(result))
    else:
        for item in result["items"]:
            typer.echo(f"{item['provider']}: {item['status']}")
        if not result["items"]:
            typer.echo("No saved agent connections.")


@app.command()
@handled
def disconnect(
    provider: Provider = typer.Option(..., "--provider"),
    as_json: bool = typer.Option(False, "--json"),
):
    result = AccountClient(service_url()).authenticated(
        "DELETE", f"/v1/agent-connections/{provider}"
    )
    typer.echo(
        json.dumps(result)
        if as_json
        else f"{provider}: cloud connection removed. Provider login has not been revoked."
    )


@app.command()
@handled
def integrations(
    as_json: bool = typer.Option(False, "--json"),
    provider: Provider = typer.Option("codex", "--provider"),
):
    result = api("GET", f"/v1/agent-connections/{provider}/integrations")
    if as_json:
        typer.echo(json.dumps(result))
    else:
        for item in result["items"]:
            typer.echo(
                f"{item['name']} ({item['id']}): {'available' if item['available'] else 'unavailable'}"
            )


@app.command()
@handled
def configure(
    provider: Provider = typer.Option("codex", "--provider"),
    integration: list[str] | None = typer.Option(None, "--integration"),
    all_integrations: bool = typer.Option(False, "--all-integrations"),
    no_integrations: bool = typer.Option(False, "--no-integrations"),
    as_json: bool = typer.Option(False, "--json"),
):
    chosen = select(integration, all_integrations, no_integrations, as_json, provider)
    result = api("PATCH", f"/v1/agent-connections/{provider}", {"integrations": chosen})
    typer.echo(json.dumps(result) if as_json else f"{provider} defaults saved for future VMs.")
