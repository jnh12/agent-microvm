import json

from agent_sandbox.cli.agent_connections import app
from typer.testing import CliRunner


def test_oauth_json_commands_do_not_read_local_credentials(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "a" * 32, "status": "pending"}

    monkeypatch.setattr("agent_sandbox.cli.agent_connections.api", api)
    runner = CliRunner()
    for provider in ("codex", "claude"):
        result = runner.invoke(app, ["login", "--provider", provider, "--replace", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "pending"
        assert calls[-1] == ("POST", f"/v1/agent-connections/{provider}/login", {"replace": True})
    for command, method in [("login-status", "GET"), ("login-cancel", "DELETE")]:
        result = runner.invoke(app, [command, "a" * 32, "--json"])
        assert result.exit_code == 0, result.output
        assert calls[-1] == (method, "/v1/agent-logins/" + "a" * 32, None)
    result = runner.invoke(app, ["login-complete", "a" * 32, "--json"], input="private-auth-code")
    assert result.exit_code == 0, result.output
    assert calls[-1][2] == {"code": "private-auth-code"}
    assert "private-auth-code" not in result.output
