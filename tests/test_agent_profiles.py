import json

import pytest
from agent_sandbox.accounts import AccountError
from agent_sandbox.cli.agent_connections import app
from agent_sandbox.cli.agent_profiles import select
from typer.testing import CliRunner


@pytest.fixture
def api(monkeypatch):
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/integrations"):
            return {
                "items": [
                    {"id": "gh", "name": "GitHub", "available": True},
                    {"id": "mail", "name": "Gmail", "available": True},
                    {"id": "blocked", "name": "Blocked", "available": False},
                ]
            }
        if method == "DELETE":
            return {"name": "dev", "status": "deleted"}
        if method == "GET" and path == "/v1/agent-profiles/dev":
            return {"name": "dev", "provider": "codex", "integrations": []}
        if method == "GET":
            return {"items": []}
        return {"name": "dev", **body}

    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", request)
    monkeypatch.setattr("agent_sandbox.cli.agent_connections.api", request)
    return calls


def test_profile_commands_use_ids_and_allow_explicit_empty(api):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["profile", "create", "dev", "--integration", "github", "--integration", "Gmail", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["integrations"] == ["gh", "mail"]
    assert api[-1] == (
        "POST",
        "/v1/agent-profiles",
        {"name": "dev", "provider": "codex", "integrations": ["gh", "mail"]},
    )
    assert (
        runner.invoke(app, ["profile", "update", "dev", "--no-integrations", "--json"]).exit_code
        == 0
    )
    assert api[-1][2] == {"integrations": []}
    assert runner.invoke(app, ["profile", "list", "--json"]).exit_code == 0
    assert runner.invoke(app, ["profile", "delete", "dev", "--json"]).exit_code == 0


def test_unattended_requires_selection_and_rejects_unavailable(api):
    runner = CliRunner()
    result = runner.invoke(app, ["profile", "create", "dev", "--json"])
    assert result.exit_code == 1 and "SELECTION_REQUIRED" in result.output and not api
    with pytest.raises(AccountError):
        select(["blocked"], unattended=True)
    with pytest.raises(AccountError):
        select(["github"], no_integrations=True)


def test_interactive_selection_and_default_configuration(api):
    runner = CliRunner()
    response = runner.invoke(app, ["profile", "create", "dev"], input="1,2\n")
    assert response.exit_code == 0, response.output
    assert api[-1][2]["integrations"] == ["gh", "mail"]
    response = runner.invoke(app, ["configure", "--all-integrations", "--json"])
    assert response.exit_code == 0, response.output
    assert api[-1] == ("PATCH", "/v1/agent-connections/codex", {"integrations": "all"})


def test_codex_connect_requires_explicit_unattended_selection_before_upload(
    api, monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.service_url", lambda: "https://example.test"
    )
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.AccountClient.authenticated",
        lambda *a, **kw: calls.append(kw) or {"provider": "codex", "status": "stored_unverified"},
    )
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "secret-access",
                    "refresh_token": "secret-refresh",
                    "id_token": "secret-id",
                }
            }
        )
    )
    args = ["connect", "--provider", "codex", "--credentials-file", str(path), "--yes", "--json"]
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and not calls
    result = runner.invoke(app, [*args, "--integration", "github"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["integrations"] == ["gh"]
    assert "secret-" not in result.output and len(calls) == 1


def test_claude_profile_routes_to_provider(api):
    result = CliRunner().invoke(
        app,
        ["profile", "create", "dev", "--provider", "claude", "--integration", "Gmail", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert api[0][1] == "/v1/agent-connections/claude/integrations"
    assert api[-1][2]["provider"] == "claude"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_all_is_persistent_mode_without_inventory_snapshot(api, provider):
    result = CliRunner().invoke(
        app,
        ["profile", "create", "everything", "--provider", provider, "--all-integrations", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert api == [
        (
            "POST",
            "/v1/agent-profiles",
            {"name": "everything", "provider": provider, "integrations": "all"},
        )
    ]

def test_profile_model_only_update_preserves_connections(monkeypatch):
    from agent_sandbox.cli.agent_profiles import app
    from typer.testing import CliRunner
    calls = []
    def api(method, path, body=None):
        calls.append((method, body))
        return {'name':'dev','provider':'codex','model':'model-a','integrations':['github']}
    monkeypatch.setattr('agent_sandbox.cli.agent_profiles.api', api)
    result = CliRunner().invoke(app, ['update','dev','--model','model-a'])
    assert result.exit_code == 0, result.output
    assert calls[-1] == ('PATCH', {'model':'model-a'})
    assert 'model-a' in result.output
