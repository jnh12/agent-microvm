import json

import pytest
from agent_sandbox.cli.agent_connections import app
from agent_sandbox.cli.main import app as main_app
from typer.testing import CliRunner


def test_send_queue_and_request_identity(monkeypatch):
    from agent_sandbox.cli import agent_runs

    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "run1", "status": "queued", "session_id": "s1"}

    monkeypatch.setattr(agent_runs, "api", api)
    result = CliRunner().invoke(
        app,
        [
            "send",
            "dev",
            "hello",
            "--queue",
            "--session",
            "review",
            "--request-key",
            "retry",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0] == (
        "POST",
        "/v1/vms/dev/agent/runs",
        {"prompt": "hello", "session": "review", "queue": True, "request_key": "retry"},
    )
    assert json.loads(result.output)["request_key"] == "retry"


def test_prompt_file_and_conflict_before_request(monkeypatch, tmp_path):
    from agent_sandbox.cli import agent_runs

    file = tmp_path / "prompt.txt"
    file.write_text("hello")
    calls = []
    monkeypatch.setattr(
        agent_runs, "api", lambda *args: calls.append(args) or {"id": "run1", "status": "starting"}
    )
    runner = CliRunner()
    assert runner.invoke(app, ["send", "dev", "hello", "--prompt-file", str(file)]).exit_code != 0
    assert calls == []
    assert runner.invoke(app, ["send", "dev", "--prompt-file", str(file), "--json"]).exit_code == 0
    assert calls[0][2]["prompt"] == "hello"


def test_create_prompt_is_durable_in_the_single_creation_request(monkeypatch):
    from agent_sandbox.cli import remote

    calls = []
    monkeypatch.setattr(remote, "remote_url", lambda: "https://example.test")

    def request(method, path, body):
        calls.append((method, path, body))
        return {"id": None, "state": "CREATING", "status": "preparing"}

    monkeypatch.setattr(remote, "request", request)
    command = [
        "create",
        "dev",
        "--agent",
        "claude",
        "--prompt",
        "hello",
        "--request-key",
        "retry",
        "--json",
    ]
    for _ in range(2):
        result = CliRunner().invoke(main_app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "preparing"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][1] == "/v1/vms"
    assert calls[0][2]["prompt"] == "hello" and calls[0][2]["wait"] is False


def test_create_validates_prompt_before_creating_vm(monkeypatch):
    from agent_sandbox.cli import remote

    monkeypatch.setattr(remote, "remote_url", lambda: "https://example.test")
    calls = []
    monkeypatch.setattr(remote, "request", lambda *a: calls.append(a) or {"items": []})
    result = CliRunner().invoke(main_app, ["create", "dev", "--prompt", "hello"])
    assert result.exit_code != 0 and not any(call[0] == "POST" for call in calls)


def test_send_model_transport(monkeypatch):
    from agent_sandbox.cli.agent_runs import submit

    calls = []
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_runs.api", lambda *args: calls.append(args) or {"id": "run"}
    )
    submit("dev", "hello", model="model-a")
    assert calls[-1][2]["model"] == "model-a"
    submit("dev", "hello")
    assert "model" not in calls[-1][2]


@pytest.mark.parametrize(
    "status,code", [("completed", 0), ("failed", 1), ("cancelled", 1), ("interrupted", 1)]
)
@pytest.mark.parametrize("as_json", [True, False])
def test_follow_drains_events_published_between_empty_page_and_terminal_status(
    monkeypatch, status, code, as_json
):
    from agent_sandbox.cli import agent_runs

    event = {
        "type": "output",
        "data": {"method": "item/agentMessage/delta", "params": {"delta": "FINAL_REPLY"}},
    }
    pages = iter(
        [
            {"items": [], "cursor": 7},
            {"items": [event], "cursor": 8},
            {"items": [], "cursor": 8},
        ]
    )
    cursors = []

    def api(method, path):
        assert method == "GET"
        if "/events?" in path:
            cursors.append(path.rsplit("=", 1)[1])
            return next(pages)
        return {"id": "r1", "status": status}

    monkeypatch.setattr(agent_runs, "api", api)
    monkeypatch.setattr(agent_runs.time, "sleep", lambda _: pytest.fail("terminal drain slept"))
    command = ["logs", "dev", "--run", "r1", "--after", "7", "--follow"]
    result = CliRunner().invoke(app, command + (["--json"] if as_json else []))
    assert result.exit_code == code, result.output
    assert cursors == ["7", "7", "8"]
    if as_json:
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert [record["type"] for record in records] == ["events"] * 3 + ["result"]
        assert records[1]["events"] == [event]
        assert records[-1] == {"type": "result", "run": {"id": "r1", "status": status}, "cursor": 8}
    else:
        assert result.output.count("FINAL_REPLY") == 1
        assert "r1: " + status in result.output


def test_terminal_drain_respects_deadline(monkeypatch):
    from agent_sandbox.cli import agent_runs

    clock = iter([0, 301])
    monkeypatch.setattr(agent_runs.time, "monotonic", lambda: next(clock))
    calls = []

    def api(method, path):
        calls.append(path)
        if "/events?" in path:
            return {"items": [], "cursor": 7}
        return {"id": "r1", "status": "completed"}

    monkeypatch.setattr(agent_runs, "api", api)
    result = agent_runs.follow("r1", as_json=True)
    assert result == {"id": "r1", "status": "completed"}
    assert len(calls) == 2
