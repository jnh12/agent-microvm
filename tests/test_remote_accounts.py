import httpx
import pytest
from agent_sandbox import accounts, remote
from agent_sandbox.errors import SandboxError


def test_remote_transport_sends_service_scoped_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("VMNEST_API_URL", "https://service.test")
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    seen = []

    def secret(url, slot):
        assert (url, slot) == ("https://service.test", "session")
        return "test-session"

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-session"
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(accounts, "secret", secret)
    client = httpx.Client
    monkeypatch.setattr(
        remote.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)
    )
    assert remote.request("GET", "/v1/vms") == {"items": []}
    assert len(seen) == 1


def test_missing_session_does_not_fall_back_to_aws(monkeypatch, tmp_path):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("VMNEST_API_URL", "https://service.test")
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.setattr(accounts, "secret", lambda *args: None)
    monkeypatch.setattr(remote.httpx, "Client", lambda **kw: pytest.fail("network called"))
    with pytest.raises(SandboxError, match="Sign in"):
        remote.request("GET", "/v1/vms")


def test_saved_hosted_mode_and_explicit_direct_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("VMNEST_API_URL", raising=False)
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    accounts.save_config({"mode": "hosted", "url": "https://service.test"})
    assert remote.remote_url() == "https://service.test"
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    assert remote.remote_url() == ""


def test_account_client_filters_private_fields(monkeypatch):
    monkeypatch.setattr(
        accounts.AccountClient,
        "authenticated",
        lambda *args: {
            "id": "user",
            "login": "alice",
            "is_admin": False,
            "token": "synthetic-session",
            "secret": "synthetic-private",
            "password_hash": "synthetic-hash",
        },
    )
    value = accounts.AccountClient("https://service.test").account()
    assert value == {"id": "user", "login": "alice", "is_admin": False}


def test_cli_whoami_never_prints_private_account_fields(monkeypatch):
    import json

    from agent_sandbox.cli.main import app
    from typer.testing import CliRunner

    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "https://service.test")
    monkeypatch.setattr(
        accounts.AccountClient,
        "authenticated",
        lambda *args: {
            "id": "user",
            "login": "alice",
            "secret": "synthetic-secret",
        },
    )
    result = CliRunner().invoke(app, ["whoami", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["account"] == {"id": "user", "login": "alice"}
    assert "synthetic-secret" not in result.output
