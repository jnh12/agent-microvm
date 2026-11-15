import pytest
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


def test_usage_shows_allowance(monkeypatch):
    value = {
        "plan": "starter",
        "used_seconds": 5400,
        "hours": 50,
        "machine_count": 2,
        "machine_limit": 30,
    }
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_profiles.api",
        lambda method, path: value if (method, path) == ("GET", "/v1/billing") else None,
    )
    result = CliRunner().invoke(app, ["usage", "--json"])
    assert result.exit_code == 0, result.output
    import json

    assert json.loads(result.output) == value
    assert CliRunner().invoke(app, ["usage"]).exit_code == 0


@pytest.fixture
def settings_api(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    calls = []
    current = {
        "name": "dev",
        "label": "My machine",
        "state": "TERMINATED",
        "idle_minutes": 5,
        "agent": {"provider": "claude"},
    }

    def api(method, path, body=None):
        calls.append((method, path, body))
        return current if method == "GET" else {"status": "saved"}

    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", api)
    return calls, current


def test_rename_only_changes_display_label(settings_api):
    calls, _ = settings_api
    result = CliRunner().invoke(app, ["rename", "dev", " New name ", "--json"])
    assert result.exit_code == 0, result.output
    assert calls == [("POST", "/v1/vms/dev/rename", {"label": "New name"})]
    assert CliRunner().invoke(app, ["rename", "dev", "   "]).exit_code != 0
    assert len(calls) == 1


def test_configure_preserves_untouched_fields(settings_api):
    calls, _ = settings_api
    result = CliRunner().invoke(app, ["configure", "dev", "--idle-minutes", "10", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "POST",
        "/v1/vms/dev/configuration",
        {"label": "My machine", "idle_minutes": 10},
    )


def test_configure_agent_defaults(settings_api, monkeypatch):
    calls, _ = settings_api

    def select(names, **kwargs):
        assert names == ["GitHub"] and kwargs["provider"] == "claude"
        return ["github-id"]

    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.select", select)
    result = CliRunner().invoke(
        app,
        ["configure", "dev", "--model", "default", "--effort", "high", "--integration", "GitHub"],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][2] == {
        "label": "My machine",
        "idle_minutes": 5,
        "model": "default",
        "effort": "high",
        "integrations": ["github-id"],
    }


def test_configure_rejects_agent_changes_while_running(settings_api):
    calls, current = settings_api
    current["state"] = "RUNNING"
    result = CliRunner().invoke(app, ["configure", "dev", "--no-integrations"])
    assert result.exit_code != 0 and "Power off" in result.output
    assert all(c[0] == "GET" for c in calls)


def test_configure_read_and_clear(settings_api):
    calls, _ = settings_api
    assert CliRunner().invoke(app, ["configure", "dev", "--json"]).exit_code == 0
    assert len(calls) == 1 and calls[0][0] == "GET"
    result = CliRunner().invoke(app, ["configure", "dev", "--no-integrations"])
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["integrations"] == []


def test_configure_invalid_options_do_not_call_api(settings_api):
    calls, _ = settings_api
    for args in [
        ["--idle-minutes", "nan"],
        ["--idle-minutes", "16"],
        ["--integration", "GitHub", "--no-integrations"],
    ]:
        assert CliRunner().invoke(app, ["configure", "dev", *args]).exit_code != 0
    assert calls == []
