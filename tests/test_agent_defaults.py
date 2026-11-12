import pytest
from agent_sandbox.accounts import read_config, save_config
from agent_sandbox.cli.agent_defaults import resolve_agent
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


@pytest.mark.parametrize(
    "providers,expected",
    [([], None), (["codex"], "codex"), (["claude"], "claude"), (["codex", "claude"], None)],
)
def test_auto(monkeypatch, providers, expected):
    monkeypatch.setenv("VMNEST_API_URL", "https://example.com")
    assert (
        resolve_agent(None, lambda *a: {"items": [{"provider": p} for p in providers]}) == expected
    )


def test_default_and_overrides(monkeypatch):
    monkeypatch.setenv("VMNEST_API_URL", "https://example.com")
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.api", lambda *a: {"items": [{"provider": "codex"}]}
    )
    runner = CliRunner()
    assert runner.invoke(app, ["agent", "default", "codex"]).exit_code == 0

    def no_network(*a):
        raise AssertionError("explicit preferences do not require discovery")

    assert resolve_agent(None, no_network) == "codex"
    assert resolve_agent("claude", no_network) == "claude"
    assert resolve_agent("none", no_network) is None
    assert runner.invoke(app, ["agent", "default", "claude"]).exit_code != 0
    assert runner.invoke(app, ["agent", "default", "auto"]).exit_code == 0
    assert not read_config()["agent_defaults"]


def test_inferred_create_prompt(monkeypatch):
    monkeypatch.setenv("VMNEST_API_URL", "https://example.com")
    calls = []

    def request(method, path, body=None):
        if method == "GET":
            return {"items": [{"provider": "claude"}]}
        calls.append(body)
        return {"state": "CREATING"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    runner = CliRunner()
    result = runner.invoke(app, ["create", "dev", "--prompt", "hello"])
    assert result.exit_code == 0, result.output
    assert calls[-1]["agent"] == "claude"
    assert calls[-1]["prompt"] == "hello"
    result = runner.invoke(app, ["create", "plain", "--agent", "none"])
    assert result.exit_code == 0, result.output
    assert "agent" not in calls[-1]


def test_service_scoping(monkeypatch):
    save_config({"agent_defaults": {"https://other.example": "codex"}})
    monkeypatch.setenv("VMNEST_API_URL", "https://example.com")
    assert resolve_agent(None, lambda *a: {"items": []}) is None
