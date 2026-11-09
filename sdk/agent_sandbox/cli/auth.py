"""Browser authorization for the existing vmnest command."""

import os
import time
import webbrowser
from urllib.parse import urlparse

import typer
from rich.console import Console

from agent_sandbox.errors import SandboxError
from agent_sandbox.hosted import DEFAULT_API_URL, HostedClient, config_path, save_login


def register(app: typer.Typer) -> None:
    console = Console()
    token_app = typer.Typer(
        help="Manage credentials for SDKs and automation.", no_args_is_help=True
    )
    app.add_typer(token_app, name="token")

    @token_app.command("create")
    def token_create(name: str = typer.Option(..., "--name")) -> None:
        """Create a token and print it once. Store it as a secret."""
        try:
            result = HostedClient().request("POST", "/v1/tokens", json={"name": name})
            console.out(result["token"])
        except SandboxError as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    @app.command()
    def login(
        api_url: str = typer.Option(None, "--api-url", help="VMNest Cloud address."),
        no_browser: bool = typer.Option(False, "--no-browser"),
    ) -> None:
        """Authorize this computer in your browser."""
        try:
            client = HostedClient(
                api_url or os.environ.get("VMNEST_API_URL") or DEFAULT_API_URL, ""
            )
            device = client.request("POST", "/v1/auth/device")
            url = device.get("verification_uri_complete") or device["verification_uri"]
            if (
                urlparse(url).netloc != urlparse(client.url).netloc
                or urlparse(url).scheme != urlparse(client.url).scheme
            ):
                raise SandboxError("Authorization page must use the configured VMNest origin")
            console.print(f"Open {url}\nCode: {device['user_code']}", markup=False)
            if not no_browser:
                webbrowser.open(url)
            deadline = time.monotonic() + min(int(device["expires_in"]), 600)
            interval = max(1, min(int(device.get("interval", 5)), 30))
            while time.monotonic() < deadline:
                time.sleep(interval)
                result = client.request(
                    "POST", "/v1/auth/device/token", json={"device_code": device["device_code"]}
                )
                if result:
                    save_login(client.url, result["token"], result["account_id"])
                    console.print("Signed in.")
                    return
            raise SandboxError("Authorization expired. Run vmnest login again.")
        except (SandboxError, OSError, KeyError, ValueError) as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    @app.command()
    def logout() -> None:
        """Revoke this credential and remove the saved login."""
        try:
            client = HostedClient()
            if client.token:
                client.request("DELETE", "/v1/auth/token")
            config_path().unlink(missing_ok=True)
            console.print("Signed out.")
        except (SandboxError, OSError) as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    @app.command()
    def whoami() -> None:
        """Show your hosted account and limits."""
        try:
            console.print_json(data=HostedClient().request("GET", "/v1/me"))
        except SandboxError as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    @app.command()
    def pricing() -> None:
        """Show current hosted running, suspended and lifecycle rates."""
        try:
            console.print_json(data=HostedClient().request("GET", "/v1/pricing"))
        except SandboxError as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    @app.command()
    def billing() -> None:
        """Show hosted usage, reservations, payments and amount owed."""
        try:
            console.print_json(data=HostedClient().request("GET", "/v1/billing"))
        except SandboxError as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc
