import json
from types import SimpleNamespace

import agent_sandbox.cli.agent_discovery as discovery
from agent_sandbox.cli.agent_connections import clean_credentials


def auth(token):
    return json.dumps({"tokens": {"access_token": token, "refresh_token": "r", "id_token": "i"}})


def test_keyring_precedes_stale_file_and_wsl_is_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(auth("stale"))
    monkeypatch.setattr(discovery, "keychain", lambda provider: auth("current"))
    monkeypatch.setattr(discovery, "wsl_distros", lambda: ["Ubuntu"])
    monkeypatch.setattr(discovery, "wsl_read", lambda distro, provider: auth("wsl"))
    found = discovery.discover("codex", clean_credentials)
    assert list(found) == ["keychain", "wsl:Ubuntu"]
    assert found["keychain"]["tokens"]["access_token"] == "current"


def test_explicit_file_mode_does_not_read_keyring(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "file"')
    (tmp_path / "auth.json").write_text(auth("file"))
    monkeypatch.setattr(
        discovery, "keychain", lambda provider: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(discovery, "wsl_distros", lambda: [])
    assert list(discovery.discover("codex", clean_credentials)) == ["local"]


def test_wsl_utf16_and_arguments_are_not_shell_interpolated(monkeypatch):
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="Ubuntu\r\n".encode("utf-16-le"))

    monkeypatch.setattr(discovery.subprocess, "run", run)
    assert discovery.wsl_distros() == ["Ubuntu"]
    discovery.wsl_read("Name; echo secret", "codex")
    assert calls[-1][0][2] == "Name; echo secret"
    assert not calls[-1][1].get("shell")
