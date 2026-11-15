import json

import pytest
from agent_sandbox.accounts import AccountError
from agent_sandbox.cli import agent_runs
from agent_sandbox.cli.agent_connections import app
from typer.testing import CliRunner

runner = CliRunner()


def stub_run(monkeypatch, status="completed", **extra):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"id": "r1", "status": "starting"}
        if path.endswith("/agent/runs"):
            return {"items": [{"id": "r1"}]}
        return {"id": "r1", "status": status, **extra}

    monkeypatch.setattr(agent_runs, "api", api)
    return calls


@pytest.mark.parametrize("command", ["send"])
def test_stdin_is_one_prompt_without_terminal_ui(monkeypatch, command):
    calls = stub_run(monkeypatch)
    result = runner.invoke(
        app, [command, "dev", "--prompt-file", "-", "--json"], input="first\nsecond\n"
    )
    assert result.exit_code == 0, result.output
    assert calls[0][2]["prompt"] == "first\nsecond\n"
    assert len(calls) == 1
    assert json.loads(result.stdout)["id"] == "r1"


@pytest.mark.parametrize(
    ("status", "extra", "code"),
    [
        ("completed", {}, 0),
        ("failed", {}, 1),
        ("cancelled", {}, 1),
        ("interrupted", {}, 1),
        ("waiting_for_approval", {}, 3),
        ("queued", {"queue_paused": True}, 5),
    ],
)
def test_wait_returns_one_document_and_meaningful_exit(monkeypatch, status, extra, code):
    calls = stub_run(monkeypatch, status, **extra)
    result = runner.invoke(
        app, ["send", "dev", "hello", "--wait", "--json", "--request-key", "retry"]
    )
    assert result.exit_code == code, result.output
    data = json.loads(result.stdout)
    assert data["status"] == status and data["request_key"] == "retry"
    assert sum(method == "POST" for method, _, _ in calls) == 1


def test_timeout_does_not_cancel_or_resubmit(monkeypatch):
    calls = stub_run(monkeypatch, "running")
    clock = iter([0, 301])
    monkeypatch.setattr(agent_runs.time, "monotonic", lambda: next(clock))
    result = runner.invoke(app, ["send", "dev", "hello", "--wait", "--json"])
    assert result.exit_code == 4, result.output
    assert json.loads(result.stdout)["timed_out"] is True
    assert [m for m, _, _ in calls] == ["POST", "GET"]


def test_wait_existing_never_posts(monkeypatch):
    calls = stub_run(monkeypatch)
    result = runner.invoke(app, ["wait", "dev", "--run", "r1", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "completed"
    assert all(m == "GET" for m, _, _ in calls)


def test_logs_snapshot_has_cursor_and_preserves_events(monkeypatch):
    events = [{"type": "output", "data": {"text": "hello"}}]

    def api(method, path, body=None):
        if "/events?" in path:
            assert path.endswith("after=7")
            return {"items": events, "cursor": 8}
        return {"id": "r1", "status": "completed"}

    monkeypatch.setattr(agent_runs, "api", api)
    result = runner.invoke(app, ["logs", "dev", "--run", "r1", "--after", "7", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "events": events,
        "cursor": 8,
        "run": {"id": "r1", "status": "completed"},
    }


def test_logs_follow_is_typed_ndjson(monkeypatch):
    pages = iter([{"items": [{"type": "output"}], "cursor": 1}, {"items": [], "cursor": 1}])
    monkeypatch.setattr(
        agent_runs,
        "api",
        lambda m, p: next(pages) if "/events?" in p else {"id": "r1", "status": "completed"},
    )
    result = runner.invoke(app, ["logs", "dev", "--run", "r1", "--follow", "--json"])
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["type"] for r in records] == ["events", "events", "result"]
    assert records[-1]["cursor"] == 1


def test_uncertain_submission_exposes_request_key(monkeypatch):
    def api(*args):
        raise AccountError("SERVICE_ERROR", "Unavailable")

    monkeypatch.setattr(agent_runs, "api", api)
    result = runner.invoke(app, ["send", "dev", "hello", "--json", "--request-key", "retry"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["request_key"] == "retry"


def test_invalid_polling_fails_before_submission(monkeypatch):
    calls = stub_run(monkeypatch)
    monkeypatch.setenv("VMNEST_AGENT_POLL_SECONDS", "invalid")
    result = runner.invoke(app, ["send", "dev", "hello", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"
    assert not calls


def test_empty_stdin_fails_before_submission(monkeypatch):
    calls = stub_run(monkeypatch)
    result = runner.invoke(app, ["send", "dev", "--prompt-file", "-", "--json"], input="")
    assert result.exit_code == 1 and not calls
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_REQUEST"


def test_poll_error_retains_run_identity(monkeypatch):
    def api(method, path, body=None):
        if method == "POST":
            return {"id": "accepted", "status": "starting"}
        raise AccountError("SERVICE_ERROR", "Disconnected")

    monkeypatch.setattr(agent_runs, "api", api)
    result = runner.invoke(
        app, ["send", "dev", "hello", "--wait", "--json", "--request-key", "retry"]
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["run_id"] == "accepted" and data["request_key"] == "retry"


def test_remote_json_failure_is_parseable(monkeypatch):
    from agent_sandbox.cli.main import app as main_app
    from agent_sandbox.errors import SandboxError

    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://example.test")

    def request(*args):
        raise SandboxError("Unavailable")

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    for args in [["inspect", "dev", "--json"], ["wake", "dev", "--json"]]:
        result = runner.invoke(main_app, args)
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert not data["ok"]
