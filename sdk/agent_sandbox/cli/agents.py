"""Small hosted agent commands. Provider credentials are never command arguments."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import typer
from rich.console import Console

from agent_sandbox.agents import Agents
from agent_sandbox.errors import SandboxError

console = Console()


def command_errors(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (SandboxError, OSError, ValueError, TimeoutError) as exc:
            console.print(f"Error: {exc}", markup=False)
            raise typer.Exit(1) from exc

    return wrapped


def show(value):
    console.print_json(data=value)


def finish(client, run, detach):
    console.print(run["id"], markup=False)
    if detach:
        return
    for event in client.follow(run["id"]):
        if event["kind"] in ("assistant", "result", "stderr"):
            console.print(
                event.get("text", ""),
                markup=False,
                end="" if event["kind"] == "assistant" else "\n",
            )
    result = client.get("runs", run["id"])
    if result["state"] != "succeeded":
        console.print(result.get("error") or result["state"], markup=False)
        raise typer.Exit(1)


def register(app):
    groups = {}
    for name, help_text in {
        "credential": "Connect provider accounts.",
        "profile": "Agent settings and connections.",
        "connection": "Select external tools.",
        "agent": "Durable agents and prompt jobs.",
        "run": "Inspect agent runs.",
        "webhook": "Trigger agents from events.",
    }.items():
        group = typer.Typer(help=help_text, no_args_is_help=True)
        app.add_typer(group, name=name)
        groups[name] = group

    def resource_commands(group, resource):
        @group.command("list")
        @command_errors
        def list_resources(
            limit: int = typer.Option(100, min=1, max=100), offset: int = typer.Option(0, min=0)
        ):
            show(Agents().list(resource, limit=limit, offset=offset))

        if resource != "credentials":

            @group.command("show")
            @command_errors
            def show_resource(resource_id: str):
                show(Agents().get(resource, resource_id))

        if resource != "runs":

            @group.command("delete")
            @command_errors
            def delete_resource(resource_id: str):
                show(Agents().delete(resource, resource_id))

        if resource in {"connections", "agents", "webhooks"}:

            @group.command("update")
            @command_errors
            def update_resource(resource_id: str, file: Path = typer.Option(..., "--file")):
                """Update settings from a JSON file."""
                show(Agents().update(resource, resource_id, **json.loads(file.read_text())))

    for group_name, resource in (
        ("credential", "credentials"),
        ("profile", "profiles"),
        ("connection", "connections"),
        ("agent", "agents"),
        ("run", "runs"),
        ("webhook", "webhooks"),
    ):
        resource_commands(groups[group_name], resource)

    @groups["credential"].command("connect")
    @command_errors
    def connect(
        provider: str,
        name: str | None = typer.Option(None),
        from_local: bool = typer.Option(
            False, "--from-local", help="Upload only your Codex login credential to VMNest."
        ),
        auth_file: Path | None = typer.Option(None, "--auth-file"),
        api_key_env: str | None = typer.Option(None, "--api-key-env"),
    ):
        if provider not in {"codex", "claude"}:
            raise ValueError("Provider must be codex or claude")
        kind = "api_key"
        if from_local or auth_file:
            if provider != "codex" or api_key_env:
                raise ValueError(
                    "Local login import supports Codex only; choose one credential source"
                )
            path = (
                auth_file
                or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
            )
            if not path.is_file():
                raise ValueError(
                    "No Codex auth.json found. Sign in with Codex using file credential storage."
                )
            if path.stat().st_size > 65536:
                raise ValueError("Codex auth file is too large")
            value = json.loads(path.read_text(encoding="utf-8"))
            tokens = value.get("tokens") or {}
            if not tokens.get("access_token"):
                raise ValueError(
                    "The file does not contain a Codex login; use --api-key-env for API keys"
                )
            selected = {
                key: tokens[key]
                for key in ("access_token", "refresh_token", "id_token", "account_id")
                if key in tokens
            }
            secret = json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": selected,
                    **({"last_refresh": value["last_refresh"]} if "last_refresh" in value else {}),
                }
            )
            kind = "codex_login"
        elif api_key_env:
            secret = os.environ.get(api_key_env, "")
            if not secret:
                raise ValueError(f"Environment variable {api_key_env} is empty")
        else:
            secret = typer.prompt("API key", hide_input=True)
        show(
            Agents().create(
                "credentials", name=name or provider, provider=provider, kind=kind, secret=secret
            )
        )

    for action in ("verify", "discover"):

        def bind_credential_action(name):
            @groups["credential"].command(name)
            @command_errors
            def perform(credential_id: str):
                show(
                    Agents().action(
                        "credentials",
                        credential_id,
                        "connections/discover" if name == "discover" else name,
                    )
                )

        bind_credential_action(action)

    @groups["profile"].command("create")
    @command_errors
    def profile_create(
        name: str,
        provider: str = typer.Option(...),
        credential: str = typer.Option(...),
        model: str | None = typer.Option(None),
        connection: list[str] = typer.Option([], "--connection"),
        instructions: str = typer.Option(""),
        max_run_seconds: int = typer.Option(600, min=120, max=3600),
        idle_stop_seconds: int = typer.Option(300, min=60, max=1800),
    ):
        fields = dict(
            name=name,
            provider=provider,
            credential_id=credential,
            connection_ids=connection,
            instructions=instructions,
            max_run_seconds=max_run_seconds,
            idle_stop_seconds=idle_stop_seconds,
        )
        if model:
            fields["model"] = model
        show(Agents().create("profiles", **fields))

    @groups["profile"].command("update")
    @command_errors
    def profile_update(
        profile_id: str,
        file: Path = typer.Option(
            ..., "--file", help="JSON containing the profile fields to update."
        ),
    ):
        show(Agents().update("profiles", profile_id, **json.loads(file.read_text())))

    @groups["connection"].command("create")
    @command_errors
    def connection_create(
        name: str,
        kind: str = typer.Option(...),
        credential: str | None = typer.Option(None),
        app_id: str | None = typer.Option(None),
        url: str | None = typer.Option(None),
        tool: list[str] = typer.Option([], "--tool"),
        repository: list[str] = typer.Option([], "--repository"),
        write: bool = typer.Option(False, "--write"),
        token_env: str | None = typer.Option(None, "--token-env"),
    ):
        fields = dict(
            name=name, kind=kind, allowed_tools=tool, repositories=repository, write_enabled=write
        )
        fields.update(
            {
                key: value
                for key, value in {
                    "credential_id": credential,
                    "app_id": app_id,
                    "url": url,
                }.items()
                if value
            }
        )
        if token_env:
            fields["token"] = os.environ.get(token_env, "")
            if not fields["token"]:
                raise ValueError(f"Environment variable {token_env} is empty")
        show(Agents().create("connections", **fields))

    @groups["connection"].command("tools")
    @command_errors
    def connection_tools(connection_id: str):
        show(Agents().connection_tools(connection_id))

    @groups["agent"].command("create")
    @command_errors
    def agent_create(name: str, profile: str = typer.Option(...)):
        show(Agents().create("agents", name=name, profile_id=profile))

    for action in ("start", "stop"):

        def bind_agent_action(name):
            @groups["agent"].command(name)
            @command_errors
            def perform(agent_id: str):
                show(Agents().action("agents", agent_id, name))

        bind_agent_action(action)

    @groups["agent"].command("run")
    @command_errors
    def agent_run(
        prompt: str,
        profile: str | None = typer.Option(None),
        agent: str | None = typer.Option(None),
        detach: bool = typer.Option(False),
        name: str | None = typer.Option(None),
    ):
        client = Agents()
        finish(client, client.run(prompt, profile_id=profile, agent_id=agent, name=name), detach)

    @groups["agent"].command("chat")
    @command_errors
    def agent_chat(agent_id: str):
        """Continue a durable agent. /exit leaves it running until its idle stop."""
        client = Agents()
        for message in client.messages(agent_id).get("items", []):
            console.print(f"{message['role']}: {message['text']}", markup=False)
        while True:
            prompt = typer.prompt(">")
            if prompt.strip() in {"/exit", "/quit"}:
                return
            finish(client, client.message(agent_id, prompt), False)

    @groups["run"].command("events")
    @command_errors
    def run_events(
        run_id: str,
        after: int = typer.Option(0, min=0),
        follow: bool = typer.Option(False, "--follow"),
    ):
        client = Agents()
        if follow:
            for event in client.follow(run_id, after=after):
                show(event)
        else:
            show(client.events(run_id, after=after))

    @groups["run"].command("cancel")
    @command_errors
    def run_cancel(run_id: str):
        show(Agents().action("runs", run_id, "cancel"))

    @groups["run"].command("artifacts")
    @command_errors
    def run_artifacts(run_id: str):
        show(Agents().artifacts(run_id))

    @groups["run"].command("download-url")
    @command_errors
    def artifact_url(run_id: str, artifact_id: str):
        show(Agents().artifact_url(run_id, artifact_id))

    @groups["webhook"].command("create")
    @command_errors
    def webhook_create(
        name: str,
        profile: str | None = typer.Option(None),
        agent: str | None = typer.Option(None),
        provider: str = typer.Option("generic"),
        prompt: str = typer.Option("{{payload}}"),
        event: list[str] = typer.Option([], "--event"),
        daily_run_limit: int = typer.Option(10, min=1, max=20),
    ):
        if bool(profile) == bool(agent):
            raise ValueError("Choose exactly one of --profile and --agent")
        fields = dict(
            name=name,
            provider=provider,
            prompt_template=prompt,
            events=event,
            daily_run_limit=daily_run_limit,
            enabled=True,
        )
        fields["profile_id" if profile else "agent_id"] = profile or agent
        show(Agents().create("webhooks", **fields))

    @groups["webhook"].command("rotate")
    @command_errors
    def webhook_rotate(webhook_id: str):
        show(Agents().action("webhooks", webhook_id, "rotate"))

    @groups["webhook"].command("test")
    @command_errors
    def webhook_test(webhook_id: str, file: Path = typer.Option(..., "--file")):
        show(Agents().action("webhooks", webhook_id, "test", payload=json.loads(file.read_text())))
