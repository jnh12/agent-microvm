import json

import httpx
import pytest
from agent_sandbox.cli.main import app
from agent_sandbox.errors import SandboxError
from typer.testing import CliRunner


def test_schema_is_offline_and_includes_nested_commands(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    result = CliRunner().invoke(app, ["schema"])
    assert result.exit_code == 0, result.exception
    data = json.loads(result.stdout)
    commands = {c["path"][-1]: c for c in data["cli"]["commands"]}
    assert "wake" in commands and "infra" not in commands
    assert "open" not in commands
    params = {p["name"]: p for p in commands["create"]["parameters"]}
    assert params["name"]["multiple"] and "repository" not in params
    assert params["parallel"]["max"] == 16
    assert "wait" in [c["path"][-1] for c in commands["agent"]["commands"]]


def test_exec_json_preserves_bytes_and_exit_status(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    value = {"exit_code": 7, "stdout": "/wA=", "stderr": "ZXJy", "encoding": "base64"}
    monkeypatch.setattr("agent_sandbox.cli.remote.request", lambda *a: value)
    result = CliRunner().invoke(
        app, ["exec", "dev", "--json", "--request-key", "retry", "--", "false"]
    )
    assert result.exit_code == 7, result.output
    assert json.loads(result.stdout) == {**value, "request_key": "retry"}


def test_exec_failure_keeps_request_key(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def fail(*args):
        raise SandboxError("Disconnected")

    monkeypatch.setattr("agent_sandbox.cli.remote.request", fail)
    result = CliRunner().invoke(
        app, ["exec", "dev", "--json", "--request-key", "retry", "--", "true"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["request_key"] == "retry"


def test_chat_refuses_noninteractive_input_before_requests(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    result = CliRunner().invoke(app, ["agent", "chat", "dev"], input="hello\n")
    assert result.exit_code == 1 and "agent send" in result.output


def test_transport_timeout_and_sanitized_errors(monkeypatch):
    from agent_sandbox import remote

    monkeypatch.setattr(remote, "remote_url", lambda: "https://api.example.test")
    monkeypatch.setattr("agent_sandbox.accounts.secret", lambda *args: "test-token")
    monkeypatch.setenv("VMNEST_REQUEST_TIMEOUT_SECONDS", "12")
    seen = []

    class Client:
        def __init__(self, **kwargs):
            seen.append(kwargs["timeout"].read)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, *args, **kwargs):
            return httpx.Response(502, text="private provider response")

    monkeypatch.setattr(remote.httpx, "Client", Client)
    with pytest.raises(SandboxError) as error:
        remote.request("POST", "/v1/vms", {})
    assert seen == [12]
    assert "private provider response" not in str(error.value)
    monkeypatch.setenv("VMNEST_REQUEST_TIMEOUT_SECONDS", "nan")
    with pytest.raises(SandboxError, match="finite"):
        remote.request("POST", "/v1/vms", {})
    assert seen == [12]
