import json

from agent_sandbox.cli.main import app
from typer.testing import CliRunner


def test_checkpoint_restore_and_status_are_http_only(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "op", "phase": "completed", "workspace": "durable"}

    monkeypatch.setattr("agent_sandbox.cli.agent_persistence.api", api)
    runner = CliRunner()
    result = runner.invoke(
        app, ["agent", "checkpoint", "dev", "--terminate", "--request-key", "same", "--wait"]
    )
    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "POST",
        "/v1/vms/dev/agent/checkpoints",
        {"request_key": "same", "terminate": True},
    )
    assert json.loads(result.output)["workspace"] == "durable"
    result = runner.invoke(app, ["agent", "restore", "dev", "--request-key", "restore"])
    assert result.exit_code == 0, result.output
    assert calls[-1][1] == "/v1/vms/dev/agent/restore"
    assert calls[-1][2]["terminate"] is False
    assert runner.invoke(app, ["agent", "checkpoint-status", "op"]).exit_code == 0
    assert calls[-1][:2] == ("GET", "/v1/agent-state-operations/op")


def test_create_disposable_transports_flag(monkeypatch):
    calls = []
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def request(method, path, body=None):
        calls.append(body)
        return {"id": "vm", "state": "RUNNING", "disposable": body.get("disposable", False)}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    runner = CliRunner()
    result = runner.invoke(app, ["create", "scratch", "--agent", "codex", "--disposable", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1]["disposable"] is True
    assert calls[-1]["agent"] == "codex"
    result = runner.invoke(app, ["create", "saved", "--agent", "claude"])
    assert result.exit_code == 0, result.output
    assert "disposable" not in calls[-1]


def test_create_approval_option(monkeypatch):
    calls = []
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def request(method, path, body=None):
        calls.append(body)
        return {"id": "vm", "state": "RUNNING"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    runner = CliRunner()
    result = runner.invoke(app, ["create", "careful", "--agent", "claude", "--approvals"])
    assert result.exit_code == 0, result.output
    assert calls[-1]["approvals"] is True
    result = runner.invoke(app, ["create", "free", "--agent", "codex"])
    assert result.exit_code == 0, result.output
    assert "approvals" not in calls[-1]
    assert runner.invoke(app, ["create", "invalid", "--approvals"]).exit_code != 0


def test_top_level_workspace_commands(monkeypatch):
    calls = []
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "op", "phase": "completed"}

    monkeypatch.setattr("agent_sandbox.cli.agent_persistence.api", api)
    runner = CliRunner()
    result = runner.invoke(app, ["checkpoint", "normal", "--terminate", "--wait"])
    assert result.exit_code == 0, result.output
    assert calls[-1][1] == "/v1/vms/normal/checkpoints"
    assert calls[-1][2]["terminate"] is True
    result = runner.invoke(app, ["restore", "normal", "--wait"])
    assert result.exit_code == 0, result.output
    assert calls[-1][1] == "/v1/vms/normal/restore"
    assert runner.invoke(app, ["workspaces"]).exit_code == 0
    assert runner.invoke(app, ["checkpoint-status", "op"]).exit_code == 0


def test_create_project_options(monkeypatch):
    calls = []
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def request(method, path, body=None):
        calls.append(body)
        return {"id": "vm", "state": "RUNNING"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    result = CliRunner().invoke(
        app,
        [
            "create",
            "dev",
            "--repo",
            "https://github.com/octocat/Hello-World.git",
            "--setup",
            "npm install",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1]["repository"] == "https://github.com/octocat/Hello-World.git"
    assert calls[-1]["setup_command"] == "npm install"


def test_prompt_wait_exits_if_vm_stops_during_setup(monkeypatch):
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = []

    def request(method, path, body=None):
        calls.append((method, path))
        return {
            "id": "vm",
            "state": "RUNNING" if method == "POST" else "TERMINATED",
            "project": {"setup_status": "running"},
        }

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    result = CliRunner().invoke(
        app, ["create", "dev", "--agent", "codex", "--setup", "sleep 10", "--prompt", "hello"]
    )
    assert result.exit_code != 0
    assert "VM stopped before project setup completed" in result.output
    assert len(calls) == 2
