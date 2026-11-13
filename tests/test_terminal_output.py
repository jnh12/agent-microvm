import asyncio
import io
import json
import threading

from agent_sandbox.cli import agent_runs, chat
from agent_sandbox.cli.main import app
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from typer.testing import CliRunner

runner = CliRunner()


def test_inspect_default_and_json(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    response = {"name": "dev", "state": "RUNNING", "agent": {"provider": "claude"}}
    monkeypatch.setattr("agent_sandbox.cli.remote.request", lambda *a: response)
    human = runner.invoke(app, ["inspect", "dev"])
    assert human.exit_code == 0 and "Computer" in human.output
    assert "claude" in human.output and '"state"' not in human.output
    assert json.loads(runner.invoke(app, ["inspect", "dev", "--json"]).output) == response


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


def test_open_and_list_keep_physical_lifecycle_out_of_human_output(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    row = {"name": "dev", "id": "physical-secret-id", "state": "TERMINATED", "status": "sleeping"}
    monkeypatch.setattr("agent_sandbox.cli.remote.request", lambda *a: {"items": [row]})
    human = runner.invoke(app, ["ls"])
    assert human.exit_code == 0, human.output
    assert "dev" in human.output and "sleeping" in human.output
    assert "TERMINATED" not in human.output and "physical-secret-id" not in human.output
    assert json.loads(runner.invoke(app, ["ls", "--json"]).output) == {"items": [row]}
    calls = []

    def request(*args):
        calls.append(args)
        return {**row, "status": "ready", "state": "RUNNING"}

    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    opened = runner.invoke(app, ["open", "dev"])
    assert opened.exit_code == 0 and "ready" in opened.output, opened.output
    assert calls == [("POST", "/v1/vms/dev/open")]
    assert "physical-secret-id" not in opened.output


def test_create_idle_option_is_transported(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")
    calls = []

    def request(method, path, body):
        calls.append(body)
        return {"name": "dev", "id": "test", "state": "RUNNING"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    result = runner.invoke(app, ["create", "dev", "--agent", "none", "--idle-minutes", "5"])
    assert result.exit_code == 0, result.output
    assert calls[0]["idle_minutes"] == 5


def test_wait_displays_progress_and_returns_only_when_ready(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr("time.sleep", lambda _: None)
    stages = iter(["preparing", "starting", "connecting_agent", "setting_up", "ready"])
    calls = []

    def request(method, path):
        calls.append((method, path))
        return {"name": "dev", "status": next(stages)}

    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    result = runner.invoke(app, ["wait", "dev"])
    assert result.exit_code == 0, result.output
    for text in ("Preparing", "Starting", "Connecting agent", "Setting up", "Ready"):
        assert text in result.output
    assert len(calls) == 5 and all(c == ("GET", "/v1/vms/dev") for c in calls)


def test_wait_failed_startup_is_an_error(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr(
        "agent_sandbox.cli.computers.request",
        lambda *a: {"name": "dev", "status": "failed", "startup": {"error": "Setup failed"}},
    )
    result = runner.invoke(app, ["wait", "dev"])
    assert result.exit_code != 0 and "Setup failed" in result.output

def test_shutdown_and_delete_transport(monkeypatch):
    from agent_sandbox.cli.main import app
    from typer.testing import CliRunner
    monkeypatch.setenv('VMNEST_MODE','hosted')
    monkeypatch.setenv('VMNEST_API_URL','https://example.test')
    calls=[]
    def request(method,path):
        calls.append((method,path))
        return {'name':'dev','status':'deleted' if method=='DELETE' else 'sleeping'}
    monkeypatch.setattr('agent_sandbox.cli.computers.request',request)
    runner=CliRunner()
    assert runner.invoke(app,['shutdown','dev']).exit_code==0
    assert calls[-1]==('POST','/v1/vms/dev/shutdown')
    assert runner.invoke(app,['delete','dev']).exit_code==0
    assert calls[-1]==('DELETE','/v1/vms/dev?permanent=true')


def test_chat_buffers_fragments_until_complete_message():
    output = io.StringIO()
    render = chat.EventRenderer(Console(file=output, width=100, color_system=None))
    for fragment in ("Created ", "the PR ", "successfully."):
        render.render({"data": {"method": "item/agentMessage/delta", "params": {"delta": fragment}}})
    assert output.getvalue() == ""
    render.render({"data": {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "Created the PR successfully."}}}})
    assert output.getvalue().count("Created the PR successfully.") == 1


def test_busy_error_is_preserved_and_not_reported_as_uncertain(monkeypatch):
    import httpx
    import pytest
    from agent_sandbox.accounts import AccountClient, AccountError
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, *args, **kwargs):
            return httpx.Response(409, json={"error": {"code": "AGENT_BUSY", "message": "sensitive upstream detail"}})
    monkeypatch.setattr(httpx, "Client", Client)
    def api(*args):
        return AccountClient("https://example.test").request("POST", "/v1/vms/dev/agent/runs")
    monkeypatch.setattr(agent_runs, "api", api)
    with pytest.raises(AccountError) as caught:
        agent_runs.submit("dev", "hi")
    assert caught.value.code == "AGENT_BUSY"
    assert "still working" in str(caught.value)
    assert "Request key" not in str(caught.value)
    assert "sensitive" not in str(caught.value)
