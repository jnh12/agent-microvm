import json

import pytest
from agent_sandbox.accounts import AccountError
from agent_sandbox.cli.agent_connections import app, read_credentials
from typer.testing import CliRunner


def test_extracts_only_subscription_fields(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps(
            {
                "tokens": {"access_token": "a", "refresh_token": "r", "id_token": "i"},
                "history": "private",
            }
        )
    )
    assert read_credentials("codex", p) == {
        "tokens": {"access_token": "a", "refresh_token": "r", "id_token": "i"}
    }
    p.write_text(json.dumps({"OPENAI_API_KEY": "do-not-upload"}))
    with pytest.raises(AccountError):
        read_credentials("codex", p)


def test_upload_requires_consent_and_does_not_echo_secret(tmp_path, monkeypatch):
    import agent_sandbox.cli.agent_connections as mod

    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sensitive-access",
                    "refreshToken": "sensitive-refresh",
                }
            }
        )
    )
    monkeypatch.setattr(mod, "service_url", lambda: "https://example.com")
    calls = []

    class Client:
        def __init__(self, url):
            pass

        def authenticated(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"provider": "claude", "status": "stored_unverified"}

    monkeypatch.setattr(mod, "AccountClient", Client)
    monkeypatch.setattr(mod, "api", lambda *args, **kwargs: {"integrations": []})
    runner = CliRunner()
    args = [
        "connect",
        "--provider",
        "claude",
        "--credentials-file",
        str(p),
        "--json",
        "--no-integrations",
    ]
    response = runner.invoke(app, args)
    assert response.exit_code != 0 and not calls
    response = runner.invoke(app, [*args, "--yes"])
    assert response.exit_code == 0, response.output
    assert len(calls) == 1 and "sensitive-" not in response.output
