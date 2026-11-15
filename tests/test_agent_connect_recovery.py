import json
import time

import pytest
from agent_sandbox.accounts import AccountError
from agent_sandbox.cli.agent_connections import app, require_fresh_credentials
from typer.testing import CliRunner


@pytest.mark.parametrize("expiry", [None, 0, "invalid", float("nan"), float("inf"), 1000])
def test_stale_claude_upload_fails_before_network(monkeypatch, tmp_path, expiry):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "private-access",
                    "refreshToken": "private-refresh",
                    "expiresAt": expiry,
                }
            }
        )
    )
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.service_url", lambda: "https://example.test"
    )
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.AccountClient.authenticated",
        lambda *a, **kw: pytest.fail("Must not upload stale credentials"),
    )
    result = CliRunner().invoke(
        app,
        [
            "connect",
            "--provider",
            "claude",
            "--credentials-file",
            str(path),
            "--replace",
            "--yes",
            "--no-integrations",
            "--json",
        ],
    )
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "LOCAL_AUTH_EXPIRED"
    assert "claude auth login" in error["message"] and "private-" not in result.output


def test_refresh_margin():
    with pytest.raises(AccountError):
        require_fresh_credentials(
            "claude", {"claudeAiOauth": {"expiresAt": (time.time() + 100) * 1000}}
        )
    require_fresh_credentials(
        "claude", {"claudeAiOauth": {"expiresAt": (time.time() + 3600) * 1000}}
    )


def test_partial_connect_guidance_requires_fresh_local_login(monkeypatch, tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "private-access",
                    "refreshToken": "private-refresh",
                    "expiresAt": (time.time() + 3600) * 1000,
                }
            }
        )
    )
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.service_url", lambda: "https://example.test"
    )
    monkeypatch.setattr(
        "agent_sandbox.cli.agent_connections.AccountClient.authenticated",
        lambda *a, **kw: {"provider": "claude", "status": "stored_unverified"},
    )

    def failed(*a, **kw):
        raise AccountError("AGENT_RECONNECT_REQUIRED", "Reconnect")

    monkeypatch.setattr("agent_sandbox.cli.agent_connections.api", failed)
    result = CliRunner().invoke(
        app,
        [
            "connect",
            "--provider",
            "claude",
            "--credentials-file",
            str(path),
            "--yes",
            "--no-integrations",
            "--json",
        ],
    )
    assert result.exit_code == 1
    assert "claude auth login" in result.output and "--provider claude --replace" in result.output
    assert "Use vmnest agent configure." not in result.output and "private-" not in result.output


def test_configure_does_not_silently_choose_codex(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"items": [{"provider": "codex"}, {"provider": "claude"}]}
        return {"integrations": []}

    monkeypatch.setattr("agent_sandbox.cli.agent_connections.api", api)
    result = CliRunner().invoke(app, ["configure", "--no-integrations", "--json"])
    assert (
        result.exit_code == 1 and json.loads(result.output)["error"]["code"] == "PROVIDER_REQUIRED"
    )
    assert len(calls) == 1
    monkeypatch.setattr("agent_sandbox.cli.agent_models.interactive_terminal", lambda: True)
    result = CliRunner().invoke(app, ["configure", "--no-integrations"], input="claude\n")
    assert result.exit_code == 0, result.output
    assert calls[-1][1] == "/v1/agent-connections/claude"


def test_configure_single_provider_auto_selects(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append(path)
        return {"items": [{"provider": "claude"}]} if method == "GET" else {"integrations": []}

    monkeypatch.setattr("agent_sandbox.cli.agent_connections.api", api)
    result = CliRunner().invoke(app, ["configure", "--no-integrations", "--json"])
    assert result.exit_code == 0 and calls[-1].endswith("/claude")


def test_profile_name_prompts_only_interactively(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append(body)
        return body

    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", api)
    result = CliRunner().invoke(app, ["profile", "create", "--no-integrations", "--json"])
    assert result.exit_code == 1 and "NAME_REQUIRED" in result.output and not calls
    monkeypatch.setattr("agent_sandbox.cli.agent_models.interactive_terminal", lambda: True)
    result = CliRunner().invoke(
        app, ["profile", "create", "--provider", "claude", "--no-integrations"], input="support\n"
    )
    assert result.exit_code == 0, result.output
    assert calls[-1]["name"] == "support" and calls[-1]["provider"] == "claude"
