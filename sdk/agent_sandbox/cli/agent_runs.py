"""Hosted agent session commands. All orchestration belongs to the API."""

import json
import math
import os
import sys
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
    try:
        value = float(os.environ.get("VMNEST_AGENT_POLL_SECONDS", "1"))
    except ValueError:
        raise AccountError("INVALID_REQUEST", "Polling interval must be a number.") from None
    if not 0 < value < float("inf"):
        raise AccountError("INVALID_REQUEST", "Polling interval must be finite and positive.")
    return value


def prompt_text(prompt, prompt_file):
    if (prompt is None) == (prompt_file is None):
        raise AccountError("INVALID_REQUEST", "Provide a prompt or --prompt-file, not both.")
    try:
        text = (
            sys.stdin.read()
            if str(prompt_file) == "-"
            else Path(prompt_file).read_text(encoding="utf-8")
            if prompt_file
            else prompt
        )
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
        if exc.code in {
            "AGENT_BUSY",
            "VM_BUSY",
            "VM_SAVING",
            "AGENT_NOT_READY",
            "PROJECT_NOT_READY",
            "VM_NOT_RUNNING",
        }:
            raise
        error = AccountError(
            exc.code, str(exc) + " Request key: " + key + ". Reuse it when retrying."
        )
        error.request_key = key
        raise error from None
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


def wait_seconds(value):
    if not math.isfinite(value) or value <= 0:
        raise AccountError("INVALID_REQUEST", "Timeout must be finite and positive.")
    return value


def stopped(result):
    return (
        result["status"] in TERMINAL
        or result["status"] == "waiting_for_approval"
        or (result["status"] == "queued" and result.get("queue_paused"))
    )


def exit_for(result):
    if result.get("timed_out"):
        raise typer.Exit(4)
    if result["status"] in {"failed", "cancelled", "interrupted"}:
        raise typer.Exit(1)
    if result["status"] == "waiting_for_approval":
        raise typer.Exit(3)
    if result.get("queue_paused") and result["status"] == "queued":
        raise typer.Exit(5)


def wait_run(identifier, timeout):
    deadline = time.monotonic() + wait_seconds(timeout)
    interval = poll_interval()
    path = "/v1/agent-runs/" + quote(identifier, safe="")
    while True:
        result = api("GET", path)
        if stopped(result):
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "timed_out": True}
        time.sleep(min(interval, remaining))


def follow(identifier, as_json=False, after=0, wait=True, timeout=300):
    """JSON snapshots are one document; explicit following emits typed JSON lines."""
    path = "/v1/agent-runs/" + quote(identifier, safe="")
    from .events import EventRenderer

    deadline = time.monotonic() + wait_seconds(timeout)
    interval = poll_interval()
    renderer = EventRenderer() if not as_json else None
    while True:
        page = api("GET", path + "/events?after=" + str(after))
        after = page["cursor"]
        result = api("GET", path)
        if as_json and not wait:
            typer.echo(json.dumps({"run": result, "events": page["items"], "cursor": after}))
            return result
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        "type": "events",
                        "run_id": identifier,
                        "events": page["items"],
                        "cursor": after,
                    }
                )
            )
        else:
            for event in page["items"]:
                renderer.render(event)
        remaining = deadline - time.monotonic()
        if remaining <= 0 and not stopped(result):
            result = {**result, "timed_out": True}
        # Drain terminal event pages, but never beyond the local deadline.
        draining = result["status"] in TERMINAL and bool(page["items"]) and remaining > 0
        if not wait or ((stopped(result) or remaining <= 0) and not draining):
            if as_json:
                typer.echo(json.dumps({"type": "result", "run": result, "cursor": after}))
            else:
                renderer.end_stream()
                typer.echo(
                    identifier
                    + ": "
                    + result["status"]
                    + (
                        " (wait timed out; remote work continues)"
                        if result.get("timed_out")
                        else ""
                    )
                )
            return result
        if not draining:
            time.sleep(min(interval, max(0, remaining)))


def register(app):
    @app.command(
        "send", help="Submit a task. Use --prompt-file - for stdin; --json for automation."
    )
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
        timeout: float = typer.Option(300, "--timeout", min=0.1),
        as_json: bool = typer.Option(False, "--json"),
    ):
        wait_seconds(timeout)
        poll_interval()
        text = prompt_text(prompt, prompt_file)
        from .agent_models import for_computer

        model, effort = for_computer(name, model, effort, session, as_json)
        result = submit(name, text, session, queue, request_key, model, effort)
        if wait and as_json:
            try:
                final = wait_run(result["id"], timeout)
            except AccountError as exc:
                exc.run_id = result["id"]
                exc.request_key = result["request_key"]
                raise
            typer.echo(json.dumps({**final, "request_key": result["request_key"]}))
            exit_for(final)
        else:
            typer.echo(json.dumps(result) if as_json else result["id"] + ": " + result["status"])
            if wait:
                exit_for(follow(result["id"], timeout=timeout))

    @app.command("wait")
    @handled
    def wait_existing(
        name: str,
        run_id: str = typer.Option(..., "--run", help="Exact run ID returned by send."),
        timeout: float = typer.Option(300, "--timeout", min=0.1),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Wait for an existing run without submitting or replaying a prompt."""
        from .output import details

        wait_seconds(timeout)
        # Check the run belongs to the named computer before following it.
        runs = api("GET", endpoint(name))["items"]
        if not any(r["id"] == run_id for r in runs):
            raise AccountError("RUN_NOT_FOUND", "Run not found on this computer.")
        result = wait_run(run_id, timeout)
        details(result, as_json)
        exit_for(result)

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
        timeout: float = typer.Option(300, "--timeout", min=0.1),
        after: int = typer.Option(0, "--after", min=0),
        as_json: bool = typer.Option(False, "--json"),
    ):
        result = resolve(name, run_id, session)
        final = follow(result["id"], as_json, after, following, timeout)
        if following:
            exit_for(final)

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
            try:
                body["response"] = json.loads(response_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise AccountError(
                    "INVALID_REQUEST", "Response file must contain valid JSON."
                ) from None
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
        from .agent_models import for_computer
        from .chat import run_chat

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise AccountError(
                "INTERACTIVE_REQUIRED", "Use agent send --prompt-file - for automation."
            )
        model, effort = for_computer(name, model, effort, session)
        run_chat(name, session, history, model=model, effort=effort)
