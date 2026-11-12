"""Connect existing local subscription logins to a VMNest account."""

import json
import os
import shutil
from pathlib import Path
from typing import Literal

import typer

from agent_sandbox.accounts import AccountClient, AccountError, service_url
from agent_sandbox.cli.accounts import handled

app = typer.Typer(name="agent", help="Manage saved agent account connections.")
Provider = Literal["codex", "claude"]


def credential_path(provider):
    if provider == "codex":
        return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    return (
        Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        / ".credentials.json"
    )


def read_credentials(provider, path):
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        value = json.loads(path.read_text(encoding="utf-8"))
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
    except (OSError, ValueError, TypeError):
        raise AccountError(
            "LOCAL_AUTH_UNAVAILABLE",
            "No supported subscription credential file. OS-keychain and WSL discovery are not supported yet.",
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
):
    url = service_url()
    if provider is None:
        if yes or as_json or credentials_file:
            raise AccountError(
                "PROVIDER_REQUIRED", "Specify --provider codex or --provider claude."
            )
        candidates = []
        for name in ("codex", "claude"):
            installed = shutil.which(name) is not None
            path = credential_path(name)
            typer.echo(
                f"{name}: {'installed' if installed else 'not on PATH'}; credential file {'found' if path.is_file() else 'not found'}"
            )
            if installed and path.is_file():
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
            connect(provider=name, yes=False, replace=replace, credentials_file=None, as_json=False)
        return
    payload = read_credentials(provider, credentials_file or credential_path(provider))
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
