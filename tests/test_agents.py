import asyncio
import json

import boto3
import httpx
import pytest
from agent_sandbox import Agents
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


@pytest.fixture
def platform(monkeypatch):
    monkeypatch.setenv("VMNEST_API_TOKEN", "vmnest-token")
    monkeypatch.setenv("VMNEST_API_URL", "https://vmnest.example")
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.setattr(boto3, "client", lambda *args, **kw: pytest.fail("Unexpected AWS access"))
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer vmnest-token"
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "items": [{"id": 1, "kind": "result", "text": "ok"}],
                    "next_cursor": 1,
                    "complete": True,
                },
            )
        if request.url.path == "/v1/credentials":
            return httpx.Response(201, json={"id": "credential", "name": body.get("name")})
        return httpx.Response(200, json={"id": "run", "state": "succeeded"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    return calls


def test_runs_and_messages_keep_retry_key_and_provider_secrets_out(platform):
    client = Agents()
    client.run("hello", profile_id="profile", idempotency_key="retry-unchanged")
    client.message("agent/id", "continue", idempotency_key="retry-message")
    assert platform[0].headers["idempotency-key"] == "retry-unchanged"
    assert json.loads(platform[0].content) == {"prompt": "hello", "profile_id": "profile"}
    assert platform[1].headers["idempotency-key"] == "retry-message"
    assert platform[1].url.raw_path == b"/v1/agents/agent%2Fid/messages"
    assert list(client.follow("run", poll_interval=0))[0]["text"] == "ok"
    with pytest.raises(ValueError):
        client.run("bad", profile_id="profile", agent_id="agent")


def test_codex_import_is_explicit_selective_and_never_prints_secret(platform, tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "unrelated": "do-not-upload",
                },
                "extra": "do-not-upload",
                "OPENAI_API_KEY": "do-not-upload",
            }
        )
    )
    result = CliRunner().invoke(app, ["credential", "connect", "codex", "--auth-file", str(path)])
    assert result.exit_code == 0, result.output
    body = json.loads(platform[-1].content)
    assert body["kind"] == "codex_login"
    assert "do-not-upload" not in body["secret"]
    assert "private-access" not in result.output
    assert "private-refresh" not in result.output


def test_claude_oauth_import_is_rejected_before_network(platform, tmp_path):
    result = CliRunner().invoke(app, ["credential", "connect", "claude", "--from-local"])
    assert result.exit_code == 1
    assert not platform


def test_api_key_env_and_prompt_job_commands(platform, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret-api-key")
    runner = CliRunner()
    result = runner.invoke(
        app, ["credential", "connect", "claude", "--api-key-env", "TEST_PROVIDER_KEY"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(platform[-1].content)["secret"] == "secret-api-key"
    assert "secret-api-key" not in result.output
    result = runner.invoke(app, ["agent", "run", "test", "--profile", "profile", "--detach"])
    assert result.exit_code == 0, result.output
    assert platform[-1].headers["idempotency-key"]


def test_profile_does_not_grant_connections_implicitly(platform):
    result = CliRunner().invoke(
        app, ["profile", "create", "work", "--provider", "codex", "--credential", "credential"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(platform[-1].content)["connection_ids"] == []


def test_mcp_agent_submission_uses_hosted_transport_and_exposes_no_secret_import(platform):
    pytest.importorskip("mcp")
    from agent_sandbox_mcp.server import build_server

    async def check():
        server = build_server()
        names = {tool.name for tool in await server.list_tools()}
        assert {"agent_run", "agent_message", "agent_run_cancel", "agent_resources"} <= names
        assert not any(
            "credential_connect" in name or "credential_import" in name for name in names
        )
        await server.call_tool(
            "agent_run",
            {"prompt": "test", "profile_id": "profile", "idempotency_key": "mcp-retry-key"},
        )

    asyncio.run(check())
    assert platform[-1].url.path == "/v1/runs"
    assert platform[-1].headers["idempotency-key"] == "mcp-retry-key"
