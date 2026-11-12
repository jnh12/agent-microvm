import asyncio
import io
import json
import threading

import pytest
from agent_sandbox.cli import agent_runs, chat
from agent_sandbox.cli.main import app
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from typer.testing import CliRunner

runner = CliRunner()


@pytest.mark.parametrize("prefix", [[], ["agent"]])
def test_workspace_table_and_explicit_json(monkeypatch, prefix):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    response = {
        "items": [
            {
                "id": "workspace-id",
                "name": "dev",
                "checkpoint": "saved",
                "operation": {"kind": "save", "phase": "failed", "error": "Upload failed"},
            }
        ]
    }
    monkeypatch.setattr("agent_sandbox.cli.agent_persistence.api", lambda *a: response)
    human = runner.invoke(app, [*prefix, "workspaces"])
    assert human.exit_code == 0, human.output
    assert "Workspaces" in human.output and "dev" in human.output
    assert "Upload failed" in human.output and '"items"' not in human.output
    structured = runner.invoke(app, [*prefix, "workspaces", "--json"])
    assert json.loads(structured.output) == response


def test_inspect_default_and_json(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    response = {"name": "dev", "state": "RUNNING", "agent": {"provider": "claude"}}
    monkeypatch.setattr("agent_sandbox.cli.remote.request", lambda *a: response)
    human = runner.invoke(app, ["inspect", "dev"])
    assert human.exit_code == 0 and "VM details" in human.output
    assert "claude" in human.output and '"state"' not in human.output
    assert json.loads(runner.invoke(app, ["inspect", "dev", "--json"]).output) == response


def test_checkpoint_failure_is_readable_and_fails(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_persistence.api",
        lambda *a: {
            "id": "operation-id",
            "phase": "failed",
            "error": "Previous checkpoint retained",
        },
    )
    result = runner.invoke(app, ["checkpoint-status", "operation-id"])
    assert result.exit_code == 1
    assert "Previous checkpoint retained" in result.output and '"phase"' not in result.output


def test_renderer_deltas_and_completed_only_reply():
    output = io.StringIO()
    render = chat.EventRenderer(Console(file=output, width=100, color_system=None))
    render.render(
        {"data": {"method": "item/agentMessage/delta", "params": {"delta": "STREAM_REPLY"}}}
    )
    render.render(
        {
            "data": {
                "method": "item/completed",
                "params": {"item": {"type": "agentMessage", "text": "STREAM_REPLY"}},
            }
        }
    )
    render.render(
        {
            "data": {
                "method": "item/completed",
                "params": {"item": {"type": "agentMessage", "text": "FINAL_REPLY"}},
            }
        }
    )
    render.render(
        {
            "data": {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "CLAUDE_REPLY"}]},
            }
        }
    )
    assert output.getvalue().count("STREAM_REPLY") == 1
    assert "FINAL_REPLY" in output.getvalue() and "CLAUDE_REPLY" in output.getvalue()


def test_observer_limits_history_and_advances_cursor():
    calls = []

    def api(method, path):
        calls.append(path)
        if "/events?" not in path:
            return {
                "items": [
                    {"id": "new", "status": "running"},
                    {"id": "recent", "status": "completed"},
                    {"id": "old", "status": "completed"},
                ]
            }
        return {"items": [], "cursor": 9}

    observer = chat.ChatObserver(
        api, "/runs", 1, Console(file=io.StringIO()), threading.Event(), set()
    )
    observer.poll()
    observer.poll()
    assert not any("/old/" in path for path in calls)
    assert sum("/recent/" in path for path in calls) == 1
    assert "/v1/agent-runs/new/events?after=9" in calls


def test_chat_commands_and_detach_do_not_cancel(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_runs, "api", lambda *args: calls.append(args) or {"items": []})
    monkeypatch.setattr(
        agent_runs, "submit", lambda *args: calls.append(args) or {"id": "r", "status": "queued"}
    )
    result = runner.invoke(
        app,
        ["agent", "chat", "dev", "--history", "0"],
        input="\n/help\n/unknown\n/queue hello\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "Unknown command" in result.output and "Left chat" in result.output
    assert ("dev", "hello", "main", True) in calls
    assert not any("/cancel" in str(call) for call in calls)


def test_prompt_preserves_draft_during_output_and_multiline():
    async def exercise():
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                prompt = chat.make_prompt(lambda: "Running")
                task = asyncio.create_task(prompt.prompt_async())
                pipe.send_text("draft")
                for _ in range(100):
                    if prompt.default_buffer.text == "draft":
                        break
                    await asyncio.sleep(0.01)
                assert prompt.default_buffer.text == "draft"
                # Output from another task must not modify the edit buffer.
                chat.EventRenderer(Console(file=io.StringIO())).reply("BACKGROUND_REPLY")
                prompt.app.invalidate()
                pipe.send_text("\x1b\rcontinued\r")
                assert await asyncio.wait_for(task, 3) == "draft\ncontinued"

    asyncio.run(exercise())
