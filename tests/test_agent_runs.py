import json

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


def test_create_prompt_uses_separate_idempotent_submission(monkeypatch):
    from agent_sandbox.cli import agent_runs, remote

    calls = []
    monkeypatch.setattr(remote, "remote_url", lambda: "https://example.test")
    monkeypatch.setattr(
        remote,
        "request",
        lambda method, path, body: calls.append((path, body)) or {"id": "vm1", "state": "RUNNING"},
    )
    monkeypatch.setattr(
        agent_runs,
        "api",
        lambda method, path, body: (
            calls.append((path, body)) or {"id": "run1", "status": "starting"}
        ),
    )
    command = [
        "create",
        "dev",
        "--agent",
        "claude",
        "--profile",
        "mail",
        "--prompt",
        "hello",
        "--request-key",
        "retry",
        "--json",
    ]
    for _ in range(2):
        result = CliRunner().invoke(main_app, command)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["run"]["id"] == "run1"
    assert calls[0][0] == "/v1/vms"
    assert calls[1][0] == "/v1/vms/dev/agent/runs"
    assert calls[1][1]["request_key"] == calls[3][1]["request_key"]


def test_create_validates_prompt_before_creating_vm(monkeypatch):
    from agent_sandbox.cli import remote

    monkeypatch.setattr(remote, "remote_url", lambda: "https://example.test")
    calls = []
    monkeypatch.setattr(remote, "request", lambda *a: calls.append(a))
    result = CliRunner().invoke(main_app, ["create", "dev", "--prompt", "hello"])
    assert result.exit_code != 0 and not calls
