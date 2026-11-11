import asyncio

import httpx
import pytest
from agent_sandbox.agent_client import AgentClient
from agent_sandbox.cli import main
from agent_sandbox.errors import AgentError
from agent_sandbox.models import ExecResult
from typer.testing import CliRunner


def test_exec_transport_timeout_and_typed_guest_error():
    async def run():
        async def token():
            return "test-token"

        def handler(request):
            assert request.extensions["timeout"]["read"] == 100
            assert request.headers["X-aws-proxy-auth"] == "test-token"
            return httpx.Response(504, json={"detail": "command timed out"})

        async with AgentClient("https://guest.test", token) as client:
            await client._client.aclose()
            client._client = httpx.AsyncClient(
                base_url="https://guest.test", transport=httpx.MockTransport(handler)
            )
            with pytest.raises(AgentError) as error:
                await client.exec("python", ["-V"], timeout=90)
            assert error.value.status_code == 504

    asyncio.run(run())


def test_self_hosted_exec_preserves_arguments_streams_and_exit_code(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    monkeypatch.setattr(main, "_require_record", lambda name: SimpleNamespace(
        microvm_id="test", endpoint="guest.test", region="eu-central-1"
    ))

    class Sandbox:
        @classmethod
        async def attach(cls, *args, **kwargs):
            return cls()

        async def exec(self, command, args, **kwargs):
            assert command == "python" and args == ["-c", "hello world"]
            assert kwargs == {"cwd": "/tmp", "env": {"TEST": "a=b"}, "timeout": 90}
            return ExecResult(7, b"hello", b"problem")

    monkeypatch.setattr(main, "Sandbox", Sandbox)
    result = CliRunner().invoke(main.app, [
        "exec", "test", "--cwd", "/tmp", "--env", "TEST=a=b", "--timeout", "90",
        "--", "python", "-c", "hello world",
    ])
    assert result.exit_code == 7, result.output
    assert "hello" in result.stdout and "problem" in result.stderr
