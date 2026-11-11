"""Human and noninteractive account commands; no VM operations."""

from __future__ import annotations

import json
import os
import sys
import time
import webbrowser
from functools import wraps
from urllib.parse import parse_qs, urlsplit

import typer

from agent_sandbox.accounts import (
    AccountClient,
    AccountError,
    credential_store,
    mode,
    read_config,
    save_config,
    secret,
    service_url,
)


def emit(value, as_json):
    if as_json:
        typer.echo(json.dumps(value))
        return
    if "account" in value:
        typer.echo(f"Signed in as {value['account']['login']}.")
    elif value.get("status") == "signed_out":
        typer.echo("Signed out.")
    elif value.get("status") == "pending":
        typer.echo("Waiting for GitHub approval. Run vmnest login --resume to check again.")
    else:
        typer.echo(f"Mode: {value.get('mode', 'unconfigured')}")
        if value.get("aws_account"):
            typer.echo(f"AWS account: {value['aws_account']}")
        if value.get("next_command"):
            typer.echo(f"Next: {value['next_command']}")
    if value.get("service") == "reachable":
        typer.echo("Account service and credential store are ready.")


def fail(exc: AccountError, as_json=False):
    if as_json:
        emit({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, True)
    else:
        typer.echo(f"Error [{exc.code}]: {exc}", err=True)
    raise typer.Exit(1)


def handled(command):
    """Keep account errors consistent without obscuring Typer's command signatures."""

    @wraps(command)
    def invoke(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except AccountError as exc:
            fail(exc, kwargs.get("as_json", False))

    return invoke


def check_mode_override(selected):
    if selected == "self-hosted" and os.environ.get("VMNEST_API_URL"):
        raise AccountError(
            "MODE_CONFLICT", "Unset VMNEST_API_URL before selecting self-hosted mode."
        )
    override = os.environ.get("VMNEST_MODE")
    if override and override != selected:
        raise AccountError("MODE_CONFLICT", "Unset VMNEST_MODE before changing modes.")


def validate_pending(value):
    try:
        valid = (
            isinstance(value, dict)
            and isinstance(value["ticket"], str)
            and 32 <= len(value["ticket"]) <= 256
            and (
                value.get("flow") == "browser"
                or (isinstance(value["user_code"], str) and len(value["user_code"]) <= 32)
            )
            and isinstance(value["expires_at"], int)
            and isinstance(value["interval"], int)
            and 1 <= value["interval"] <= 900
        )
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise AccountError("LOGIN_INVALID", "Invalid login state. Run vmnest login again.")
    return value


def browser_url(pending, origin):
    if pending.get("flow") != "browser":
        return "https://github.com/login/device"
    value = pending.get("verification_uri", "")
    if not isinstance(value, str):
        raise AccountError("LOGIN_INVALID", "Invalid browser login URL.")
    parsed, expected = urlsplit(value), urlsplit(origin)
    query = parse_qs(parsed.query)
    if (
        parsed.scheme != expected.scheme
        or parsed.netloc != expected.netloc
        or parsed.path != "/v1/auth/github/browser/open"
        or parsed.fragment
        or set(query) != {"request"}
        or len(query["request"]) != 1
        or not 32 <= len(query["request"][0]) <= 256
    ):
        raise AccountError("LOGIN_INVALID", "The service returned an invalid browser login URL.")
    return value


def register(app: typer.Typer):
    @app.command()
    @handled
    def setup(
        selected_mode: str | None = typer.Option(None, "--mode"),
        url: str | None = typer.Option(None, "--url"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Choose hosted accounts or self-hosted AWS. Does not provision resources."""
        if selected_mode is None:
            if as_json or not sys.stdin.isatty():
                raise AccountError("MODE_REQUIRED", "Pass --mode hosted or --mode self-hosted.")
            typer.echo("1. Hosted: sign in with GitHub\n2. Self-hosted: use your AWS account")
            choice = typer.prompt("Choose", default="1")
            selected_mode = {"1": "hosted", "2": "self-hosted"}.get(choice, choice)
        if selected_mode not in ("hosted", "self-hosted"):
            raise AccountError("MODE_INVALID", "Choose hosted or self-hosted.")
        check_mode_override(selected_mode)
        config = read_config()
        if selected_mode == "hosted":
            config["url"] = service_url(url)
        config["mode"] = selected_mode
        save_config(config)
        emit(
            {
                "ok": True,
                "mode": selected_mode,
                "next_command": "vmnest login"
                if selected_mode == "hosted"
                else "vmnest infra init",
                "environment_override": os.environ.get("VMNEST_MODE"),
            },
            as_json,
        )

    @app.command()
    @handled
    def login(
        url: str | None = typer.Option(None, "--url"),
        no_browser: bool = typer.Option(False, "--no-browser"),
        device: bool = typer.Option(False, "--device"),
        resume: bool = typer.Option(False, "--resume"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Browser login by default; --device uses a code. --json starts/polls once."""
        check_mode_override("hosted")
        origin = service_url(url)
        credential_store()  # Fail before creating a provider authorization.
        client = AccountClient(origin)
        config = read_config()
        if secret(origin, "session"):
            try:
                user = client.account()
            except AccountError as exc:
                if exc.code not in ("AUTH_REQUIRED", "AUTH_EXPIRED"):
                    raise
                secret(origin, "session", delete=True)
            else:
                config.update(mode="hosted", url=origin)
                save_config(config)
                emit({"ok": True, "status": "authenticated", "account": user}, as_json)
                return
        saved = secret(origin, "pending")
        if resume:
            if not saved:
                raise AccountError("LOGIN_REQUIRED", "Start with vmnest login --json.")
            try:
                pending = validate_pending(json.loads(saved))
            except ValueError:
                raise AccountError("LOGIN_INVALID", "Run vmnest login again.") from None
        else:
            endpoint = "/v1/auth/github/start" if device else "/v1/auth/github/browser/start"
            pending = validate_pending(client.request("POST", endpoint))
            browser_url(pending, origin)
            secret(origin, "pending", json.dumps(pending))
        config.update(mode="hosted", url=origin)
        save_config(config)
        verification_uri = browser_url(pending, origin)
        endpoint = (
            "/v1/auth/github/browser" if pending.get("flow") == "browser" else "/v1/auth/github"
        )
        public = {
            "ok": True,
            "status": "pending",
            "code": "USER_APPROVAL_REQUIRED",
            "flow": pending.get("flow", "device"),
            "verification_uri": verification_uri,
            "expires_at": pending["expires_at"],
            "interval": pending["interval"],
            "next_command": "vmnest login --resume --json",
        }
        if pending.get("flow") != "browser":
            public["user_code"] = pending["user_code"]
        interactive = sys.stdin.isatty() and not as_json
        if not resume:
            if as_json:
                emit(public, True)
            else:
                typer.echo("Open " + verification_uri)
                if "user_code" in public:
                    typer.echo("Enter code: " + public["user_code"])
                typer.echo(
                    "Waiting for GitHub approval..."
                    if interactive
                    else "After approval, run vmnest login --resume"
                )
            if interactive and not no_browser:
                webbrowser.open(public["verification_uri"])
            if not interactive:
                return
        while time.time() < pending["expires_at"]:
            if interactive:
                time.sleep(pending["interval"])
            result = client.request("POST", endpoint + "/poll", body={"ticket": pending["ticket"]})
            if not isinstance(result, dict) or result.get("status") not in (
                "complete",
                "pending",
            ):
                raise AccountError("SERVICE_ERROR", "Invalid login response.")
            if result["status"] == "complete":
                if (
                    not isinstance(result.get("token"), str)
                    or not result["token"].startswith("vmn_")
                    or not isinstance(result.get("user"), dict)
                    or "expires_at" not in result
                    or (
                        result["expires_at"] is not None
                        and not isinstance(result["expires_at"], int)
                    )
                ):
                    raise AccountError("SERVICE_ERROR", "Invalid login response.")
                secret(origin, "session", result["token"])
                secret(origin, "pending", delete=True)
                try:
                    client.request("POST", endpoint + "/ack", body={"ticket": pending["ticket"]})
                except AccountError:
                    pass  # Encrypted delivery record expires in two minutes.
                emit(
                    {
                        "ok": True,
                        "status": "authenticated",
                        "account": result["user"],
                        "expires_at": result["expires_at"],
                    },
                    as_json,
                )
                return
            pending["interval"] = result.get("interval")
            validate_pending(pending)
            secret(origin, "pending", json.dumps(pending))
            public["interval"] = result["interval"]
            if not interactive:
                emit(public, as_json)
                return
        secret(origin, "pending", delete=True)
        raise AccountError("LOGIN_EXPIRED", "Login expired. Run vmnest login again.")

    @app.command()
    @handled
    def whoami(as_json: bool = typer.Option(False, "--json")):
        """Show mode and verify your hosted account, if selected."""
        selected = mode()
        if selected != "hosted":
            emit(
                {
                    "ok": True,
                    "mode": selected or "unconfigured",
                    "authentication": "not_required" if selected else "not_configured",
                    "next_command": None if selected else "vmnest setup",
                },
                as_json,
            )
            return
        origin = service_url()
        emit(
            {
                "ok": True,
                "mode": selected,
                "url": origin,
                "credential_source": "os_keyring",
                "authentication": "authenticated",
                "account": AccountClient(origin).account(),
            },
            as_json,
        )

    @app.command()
    @handled
    def logout(as_json: bool = typer.Option(False, "--json")):
        """Revoke the current server session before removing the local credential."""
        origin = service_url()
        token = secret(origin, "session")
        if token:
            try:
                AccountClient(origin).request("POST", "/v1/auth/logout", token=token)
            except AccountError as exc:
                if exc.code not in ("AUTH_REQUIRED", "AUTH_EXPIRED"):
                    raise  # Keep credential so revocation can be retried.
            secret(origin, "session", delete=True)
        secret(origin, "pending", delete=True)
        emit({"ok": True, "status": "signed_out"}, as_json)
