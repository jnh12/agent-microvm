"""Hosted agent session commands. All orchestration belongs to the API."""

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountError
from agent_sandbox.cli.accounts import handled
from agent_sandbox.cli.agent_profiles import api

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def poll_interval():
    value = float(os.environ.get("VMNEST_AGENT_POLL_SECONDS", "1"))
    if not 0 < value < float("inf"):
        raise AccountError("INVALID_REQUEST", "Polling interval must be finite and positive.")
    return value


def prompt_text(prompt, prompt_file):
    if (prompt is None) == (prompt_file is None):
        raise AccountError("INVALID_REQUEST", "Provide a prompt or --prompt-file, not both.")
    try:
        text = Path(prompt_file).read_text(encoding="utf-8") if prompt_file else prompt
    except (OSError, UnicodeError) as exc:
        raise AccountError("INVALID_REQUEST", "Could not read prompt file.") from exc
    if not text.strip():
        raise AccountError("INVALID_REQUEST", "Prompt must not be empty.")
    return text


def endpoint(name):
    return "/v1/vms/" + quote(name, safe="") + "/agent/runs"


def submit(name, text, session="main", queue=False, request_key=None, model=None, effort=None):
    key = request_key or str(uuid.uuid4())
    try:
        result = api(
            "POST",
            endpoint(name),
            {
                "prompt": text,
                "session": session,
                "queue": queue,
                "request_key": key,
                **({"model": model} if model is not None else {}),
                **({"effort": effort} if effort is not None else {}),
            },
        )
    except AccountError as exc:
        if exc.code in {"AGENT_BUSY", "VM_BUSY", "VM_SAVING", "AGENT_NOT_READY", "PROJECT_NOT_READY", "VM_NOT_RUNNING"}:
            raise
        raise AccountError(
            exc.code, str(exc) + " Request key: " + key + ". Reuse it when retrying."
        ) from None
    return {**result, "request_key": key}


def resolve(name, run_id=None, session=None):
    if run_id:
        return api("GET", "/v1/agent-runs/" + quote(run_id, safe=""))
    items = api(
        "GET", endpoint(name) + (("?session=" + quote(session, safe="")) if session else "")
    )["items"]
    if not items:
        raise AccountError("RUN_NOT_FOUND", "No agent runs found for this VM/session.")
    active = [
        r
        for r in items
        if r["status"] in {"starting", "running", "waiting_for_approval", "cancelling"}
    ]
    return active[0] if active else items[0]


def render(event, as_json):
    if as_json:
        typer.echo(json.dumps(event))
        return
    from .chat import EventRenderer

    EventRenderer().render(event)


def follow(identifier, as_json=False, after=0, wait=True):
    path = "/v1/agent-runs/" + quote(identifier, safe="")
    from .chat import EventRenderer

    renderer = EventRenderer()
    while True:
        page = api("GET", path + "/events?after=" + str(after))
        for event in page["items"]:
            if as_json:
                render(event, True)
            else:
                renderer.render(event)
        after = page["cursor"]
        result = api("GET", path)
        if (
            result["status"] in TERMINAL
            or result["status"] == "waiting_for_approval"
            or (result["status"] == "queued" and result.get("queue_paused"))
            or not wait
        ):
            if result["status"] in TERMINAL and page["items"]:
                continue  # Drain any final page before reporting completion.
            renderer.end_stream()
            typer.echo(
                json.dumps(result) if as_json else "\n" + identifier + ": " + result["status"]
            )
            return result
        time.sleep(poll_interval())


def register(app):
    @app.command("send")
    @handled
    def send(
        name: str,
        effort: str | None = typer.Option(
            None, "--effort", help="Reasoning effort; select lists choices."
        ),
        model: str | None = typer.Option(
            None, "--model", help="Model for this conversation; default resets to provider default."
        ),
        prompt: str | None = typer.Argument(None),
        prompt_file: Path | None = typer.Option(None, "--prompt-file"),
        session: str = typer.Option("main", "--session"),
        queue: bool = typer.Option(False, "--queue"),
        request_key: str | None = typer.Option(None, "--request-key"),
        wait: bool = typer.Option(False, "--wait"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        text = prompt_text(prompt, prompt_file)
        from .agent_models import for_computer

        model, effort = for_computer(name, model, effort, session, as_json)
        result = submit(name, text, session, queue, request_key, model, effort)
        typer.echo(json.dumps(result) if as_json else result["id"] + ": " + result["status"])
        if wait:
            final = follow(result["id"], as_json)
            if final["status"] in {"failed", "cancelled", "interrupted"}:
                raise typer.Exit(1)

    @app.command("sessions")
    @handled
    def sessions(name: str, as_json: bool = typer.Option(False, "--json")):
        result = api("GET", "/v1/vms/" + quote(name, safe="") + "/agent/sessions")
        from .output import table

        if as_json:
            typer.echo(json.dumps(result))
        else:
            table(
                "Conversations",
                ["Session", "ID"],
                ((item["name"], item["id"]) for item in result["items"]),
                empty="No conversations yet. Send a message to start one.",
            )

    @app.command("runs")
    @handled
    def runs(name: str, as_json: bool = typer.Option(False, "--json")):
        result = api("GET", endpoint(name))
        from .output import label, table

        if as_json:
            typer.echo(json.dumps(result))
        else:
            table(
                "Agent runs",
                ["Run", "Session", "Status"],
                ((r["id"], r["session"], label(r["status"])) for r in result["items"]),
                empty="No agent runs yet.",
            )
            if result.get("queue_paused"):
                typer.echo("Queue paused. Use vmnest agent resume-queue to continue.")

    @app.command("logs")
    @handled
    def logs(
        name: str,
        run_id: str | None = typer.Option(None, "--run"),
        session: str | None = typer.Option(None, "--session"),
        following: bool = typer.Option(False, "--follow"),
        after: int = typer.Option(0, "--after", min=0),
        as_json: bool = typer.Option(False, "--json"),
    ):
        result = resolve(name, run_id, session)
        follow(result["id"], as_json, after, following)

    @app.command("cancel")
    @handled
    def cancel(
        name: str,
        run_id: str | None = typer.Option(None, "--run"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        target = resolve(name, run_id)
        result = api("POST", "/v1/agent-runs/" + target["id"] + "/cancel")
        typer.echo(json.dumps(result) if as_json else "Cancellation requested for " + result["id"])

    @app.command("approve")
    @handled
    def approve(
        name: str,
        approval_id: str = typer.Option(..., "--approval"),
        run_id: str | None = typer.Option(None, "--run"),
        deny: bool = typer.Option(False, "--deny"),
        response_file: Path | None = typer.Option(None, "--response-file"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        target = resolve(name, run_id)
        body = {"approval_id": approval_id, "decision": "deny" if deny else "allow"}
        if response_file:
            body["response"] = json.loads(response_file.read_text(encoding="utf-8"))
        result = api("POST", "/v1/agent-runs/" + target["id"] + "/approval", body)
        typer.echo(json.dumps(result) if as_json else "Approval response saved for " + result["id"])

    @app.command("resume-queue")
    @handled
    def resume_queue(name: str, as_json: bool = typer.Option(False, "--json")):
        result = api("POST", "/v1/vms/" + quote(name, safe="") + "/agent/queue/resume")
        typer.echo(json.dumps(result) if as_json else "Agent queue resumed.")

    @app.command("chat")
    @handled
    def chat(
        name: str,
        effort: str | None = typer.Option(
            None, "--effort", help="Reasoning effort; select lists choices."
        ),
        model: str | None = typer.Option(
            None, "--model", help="Model for this conversation; default resets to provider default."
        ),
        session: str = typer.Option("main", "--session"),
        history: int = typer.Option(
            5, "--history", min=0, help="Recent completed runs to display."
        ),
    ):
        from .chat import run_chat

        from .agent_models import for_computer

        model, effort = for_computer(name, model, effort, session)
        run_chat(name, session, history, model=model, effort=effort)
