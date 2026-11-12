"""Hosted agent session commands. All orchestration belongs to the API."""

import json
import os
import threading
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


def submit(name, text, session="main", queue=False, request_key=None):
    key = request_key or str(uuid.uuid4())
    try:
        result = api(
            "POST",
            endpoint(name),
            {"prompt": text, "session": session, "queue": queue, "request_key": key},
        )
    except AccountError as exc:
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
    message = event.get("data", {})
    if event.get("type") == "prompt":
        typer.echo("You: " + message.get("text", ""))
    elif event.get("type") == "approval":
        typer.echo("\nApproval requested: " + json.dumps(message))
    elif message.get("method") == "item/agentMessage/delta":
        typer.echo(message.get("params", {}).get("delta", ""), nl=False)
    elif message.get("type") == "assistant":
        for part in message.get("message", {}).get("content", []):
            if part.get("type") == "text":
                typer.echo(part["text"])
            elif part.get("type") == "tool_use":
                typer.echo("[tool] " + part.get("name", ""))
    elif message.get("method") in ("item/started", "item/completed"):
        item = message.get("params", {}).get("item", {})
        if item.get("type") != "agentMessage":
            typer.echo("[activity] " + item.get("type", ""))


def follow(identifier, as_json=False, after=0, wait=True):
    path = "/v1/agent-runs/" + quote(identifier, safe="")
    while True:
        page = api("GET", path + "/events?after=" + str(after))
        for event in page["items"]:
            render(event, as_json)
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
        prompt: str | None = typer.Argument(None),
        prompt_file: Path | None = typer.Option(None, "--prompt-file"),
        session: str = typer.Option("main", "--session"),
        queue: bool = typer.Option(False, "--queue"),
        request_key: str | None = typer.Option(None, "--request-key"),
        wait: bool = typer.Option(False, "--wait"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        result = submit(name, prompt_text(prompt, prompt_file), session, queue, request_key)
        typer.echo(json.dumps(result) if as_json else result["id"] + ": " + result["status"])
        if wait:
            final = follow(result["id"], as_json)
            if final["status"] in {"failed", "cancelled", "interrupted"}:
                raise typer.Exit(1)

    @app.command("sessions")
    @handled
    def sessions(name: str, as_json: bool = typer.Option(False, "--json")):
        result = api("GET", "/v1/vms/" + quote(name, safe="") + "/agent/sessions")
        typer.echo(
            json.dumps(result)
            if as_json
            else "\n".join(item["name"] + " " + item["id"] for item in result["items"])
        )

    @app.command("runs")
    @handled
    def runs(name: str, as_json: bool = typer.Option(False, "--json")):
        result = api("GET", endpoint(name))
        typer.echo(
            json.dumps(result)
            if as_json
            else "\n".join(
                r["id"] + " " + r["session"] + " " + r["status"] for r in result["items"]
            )
        )

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
    def chat(name: str, session: str = typer.Option("main", "--session")):
        stop = threading.Event()

        def observe():
            cursors, finished = {}, set()
            while not stop.is_set():
                try:
                    items = api("GET", endpoint(name) + "?session=" + quote(session, safe=""))[
                        "items"
                    ]
                    for item in reversed(items):
                        identifier = item["id"]
                        if identifier in finished:
                            continue
                        page = api(
                            "GET",
                            "/v1/agent-runs/"
                            + identifier
                            + "/events?after="
                            + str(cursors.get(identifier, 0)),
                        )
                        for event in page["items"]:
                            render(event, False)
                        cursors[identifier] = page["cursor"]
                        if item["status"] in TERMINAL and not page["items"]:
                            typer.echo("\n" + identifier + ": " + item["status"])
                            finished.add(identifier)
                except AccountError as exc:
                    typer.echo(str(exc), err=True)
                    stop.set()
                stop.wait(poll_interval())

        worker = threading.Thread(target=observe, daemon=True)
        worker.start()
        typer.echo("Session " + session + ". /exit, /queue TEXT, /cancel, /allow ID, /deny ID")
        try:
            while not stop.is_set():
                try:
                    text = typer.prompt("You")
                except (EOFError, KeyboardInterrupt):
                    return
                if text == "/exit":
                    return
                try:
                    if text == "/cancel":
                        current = resolve(name, session=session)
                        api("POST", "/v1/agent-runs/" + current["id"] + "/cancel")
                    elif text.startswith(("/allow ", "/deny ")):
                        current = resolve(name, session=session)
                        command, approval = text.split(" ", 1)
                        api(
                            "POST",
                            "/v1/agent-runs/" + current["id"] + "/approval",
                            {
                                "approval_id": approval.strip(),
                                "decision": "allow" if command == "/allow" else "deny",
                            },
                        )
                    else:
                        queued = text.startswith("/queue ")
                        result = submit(name, text[7:] if queued else text, session, queued)
                        typer.echo(result["id"] + ": " + result["status"])
                except AccountError as exc:
                    typer.echo(str(exc), err=True)
        finally:
            stop.set()
            worker.join(timeout=2)
