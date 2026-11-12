from agent_sandbox.cli.main import app
from typer.testing import CliRunner


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


def test_create_does_not_wait_for_project_or_prompt(monkeypatch):
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        return {"id": None, "state": "CREATING", "status": "preparing"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    result = CliRunner().invoke(
        app, ["create", "dev", "--agent", "codex", "--setup", "sleep 10", "--prompt", "hello"]
    )
    assert result.exit_code == 0, result.output
    assert "vmnest wait dev" in result.output
    assert len(calls) == 1 and calls[0][2]["prompt"] == "hello"
