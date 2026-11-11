import json

import agent_sandbox.accounts as accounts
import pytest
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.delenv("VMNEST_URL", raising=False)


def test_welcome_and_noninteractive_setup():
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0 and "Self-hosted" in result.output
    result = runner.invoke(app, ["setup", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "MODE_REQUIRED"


def test_self_hosted_requires_no_account_or_credential_store(monkeypatch):
    monkeypatch.setattr(accounts, "credential_store", lambda: pytest.fail("keyring accessed"))
    runner = CliRunner()
    assert runner.invoke(app, ["setup", "--mode", "self-hosted", "--json"]).exit_code == 0
    result = runner.invoke(app, ["whoami", "--json"])
    assert json.loads(result.output)["authentication"] == "not_required"
    assert runner.invoke(app, ["infra", "--help"]).exit_code == 0


def test_hosted_blocks_unimplemented_commands_before_aws(monkeypatch):
    accounts.save_config({"mode": "hosted", "url": "https://accounts.example.com"})
    runner = CliRunner()
    for command in ["run", "infra", "image", "mcp", "stop", "start"]:
        result = runner.invoke(app, [command])
        assert result.exit_code == 1 and "HOSTED_VMS_UNAVAILABLE" in result.output
    from agent_sandbox_mcp.server import build_server

    with pytest.raises(accounts.AccountError, match="Hosted MCP"):
        build_server()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com?token=x",
        "file:///tmp/x",
        "https://example.com/path",
        "https://example.com/#secret",
    ],
)
def test_unsafe_service_urls_rejected(url):
    with pytest.raises(accounts.AccountError):
        accounts.normalize_url(url)


def test_no_plaintext_keyring_fallback(monkeypatch):
    monkeypatch.setattr(accounts.keyring, "get_keyring", lambda: object())
    result = CliRunner().invoke(app, ["login", "--url", "https://example.com", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "CREDENTIAL_STORE_UNAVAILABLE"


def test_logout_preserves_credential_when_revocation_fails(monkeypatch):
    accounts.save_config({"mode": "hosted", "url": "https://example.com"})
    calls = []

    def secret(url, slot, value=None, delete=False):
        calls.append((slot, delete))
        return "vmn_secret"

    def request(*args, **kwargs):
        raise accounts.AccountError("SERVICE_UNAVAILABLE", "Offline")

    import agent_sandbox.cli.accounts as commands

    monkeypatch.setattr(commands, "secret", secret)
    monkeypatch.setattr(accounts.AccountClient, "request", request)
    result = CliRunner().invoke(app, ["logout", "--json"])
    assert result.exit_code == 1
    assert calls == [("session", False)]
    assert "vmn_secret" not in result.output


def test_service_transport_does_not_follow_redirect_or_expose_error_body(monkeypatch):
    import httpx

    original = httpx.Client

    def transport(request):
        return httpx.Response(
            302, headers={"Location": "https://attacker.example"}, text="secret-provider-token"
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(transport), **kw)
    )
    with pytest.raises(accounts.AccountError) as exc:
        accounts.AccountClient("https://example.com").request("GET", "/v1/account", token="secret")
    assert "secret" not in str(exc.value)


def test_mode_override_conflict_does_not_switch_routes(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    runner = CliRunner()
    for args in (["setup", "--mode", "hosted"], ["login"]):
        result = runner.invoke(app, args + ["--json"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["code"] == "MODE_CONFLICT"
    assert not accounts.config_path().exists()


def test_corrupt_config_is_an_actionable_error():
    accounts.config_path().write_text("not json")
    result = CliRunner().invoke(app, ["whoami", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "CONFIG_INVALID"


def test_unconfigured_service_does_not_start_login():
    result = CliRunner().invoke(app, ["login", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "SERVICE_NOT_CONFIGURED"


def test_invalid_saved_login_is_not_echoed(monkeypatch):
    import agent_sandbox.cli.accounts as commands

    monkeypatch.setattr(commands, "credential_store", lambda: object())
    monkeypatch.setattr(
        commands, "secret", lambda url, slot: None if slot == "session" else "secret"
    )
    result = CliRunner().invoke(
        app, ["login", "--url", "https://example.com", "--resume", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "LOGIN_INVALID"
    assert "secret" not in result.output


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/v1/auth/github/browser/open?request=" + "x" * 43,
        "https://example.com/other?request=" + "x" * 43,
        "https://example.com/v1/auth/github/browser/open?ticket=secret",
        None,
    ],
)
def test_browser_login_rejects_untrusted_open_targets(url):
    from agent_sandbox.cli.accounts import browser_url

    with pytest.raises(accounts.AccountError):
        browser_url({"flow": "browser", "verification_uri": url}, "https://example.com")
