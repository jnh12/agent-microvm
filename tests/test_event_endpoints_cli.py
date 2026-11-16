from agent_sandbox.cli.main import app
from typer.testing import CliRunner


def test_endpoint_commands_and_profile_selection(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    calls = []
    row = {
        "id": "a" * 32,
        "name": "orchestrator",
        "url": "https://receiver.example/events",
        "events": ["agent.completed"],
        "enabled": True,
    }

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/v1/event-endpoints":
            return {"items": [row]}
        if method == "GET" and path.startswith("/v1/agent-profiles/"):
            return {"name": "support", "provider": "codex", "integrations": []}
        return {"name": "support", **row, **(body or {})}

    monkeypatch.setattr("agent_sandbox.cli.event_endpoints.api", api)
    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", api)
    runner = CliRunner()
    for args in [
        ["endpoint", "create", "orchestrator", "--url", row["url"]],
        ["endpoint", "update", "orchestrator", "--disabled"],
        ["endpoint", "bind", "dev", "--endpoint", "orchestrator"],
        ["endpoint", "bindings", "dev"],
        ["endpoint", "test", "orchestrator"],
        ["endpoint", "deliveries", "orchestrator"],
        ["endpoint", "rotate", "orchestrator"],
        ["agent", "profile", "update", "support", "--endpoint", "orchestrator"],
    ]:
        result = runner.invoke(app, [*args, "--json"])
        assert result.exit_code == 0, result.output
    assert calls[-1][2]["event_endpoints"] == [row["id"]]
    assert any(
        method == "PUT"
        and path == "/v1/vms/dev/event-endpoints"
        and body == {"endpoints": [row["id"]]}
        for method, path, body in calls
    )
    assert runner.invoke(app, ["endpoint", "bind", "dev", "--json"]).exit_code != 0
    assert (
        runner.invoke(
            app, ["endpoint", "bind", "dev", "--endpoint", "orchestrator", "--clear", "--json"]
        ).exit_code
        != 0
    )
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    assert runner.invoke(app, ["endpoint", "list", "--json"]).exit_code != 0
