"""Hosted clients must work without AWS credentials and keep account state isolated."""

import asyncio
import base64
import json
import os

import boto3
import httpx
import pytest
from agent_sandbox import Sandbox
from agent_sandbox.cli.main import app
from agent_sandbox.cli.state import StateStore
from agent_sandbox.errors import ControlPlaneError, SandboxError
from agent_sandbox.hosted import HostedClient, credentials, save_login
from typer.testing import CliRunner


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "auth.json"))
    monkeypatch.setenv("VMNEST_API_URL", "https://cloud.example")
    monkeypatch.setenv("VMNEST_API_TOKEN", "account-one")
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("Hosted mode called AWS"))
    calls = []
    state = {
        "id": "sb-one",
        "name": "work",
        "state": "running",
        "image": "default",
        "endpoint": "https://guest.example",
    }

    def handle(request):
        calls.append(request)
        path = request.url.path
        if request.url.host == "guest.example":
            assert request.headers["x-aws-proxy-auth"] == "guest-token"
            if path == "/v1/exec":
                return httpx.Response(
                    200,
                    json={
                        "exit_code": 0,
                        "stdout": base64.b64encode(b"hello").decode(),
                        "stderr": "",
                    },
                )
            if path == "/v1/fs/read":
                return httpx.Response(200, json={"content": "AAEC"})
            return httpx.Response(200, json={})
        assert request.headers["authorization"] == "Bearer account-one"
        if path == "/v1/sandboxes":
            if request.method == "POST":
                assert request.headers["idempotency-key"]
                body = json.loads(request.content)
                assert body == {"name": "work", "duration": 3600, "image": "default"}
                return httpx.Response(201, json=state)
            return httpx.Response(200, json=[state])
        if path.endswith("/connection"):
            assert request.url.params["port"] == "8080"
            return httpx.Response(
                200, json={"endpoint": state["endpoint"], "token": "guest-token", "expires_in": 60}
            )
        if path.endswith("/state"):
            state["state"] = json.loads(request.content)["state"]
        if path == "/v1/me":
            return httpx.Response(200, json={"id": "account-one"})
        if path.endswith("/logs"):
            return httpx.Response(
                200,
                json={"events": [{"message": "logged", "timestamp": 123}], "next_cursor": "next"},
            )
        if path.endswith("/metrics"):
            return httpx.Response(200, json={"CPUUtilization": 12.5, "MemoryUtilization": None})
        return httpx.Response(200, json=state)

    transport = httpx.MockTransport(handle)
    sync_client, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync_client(transport=transport, **kw))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw))
    return calls, state


def test_sdk_lifecycle_guest_files_and_cloud_names_without_aws(cloud):
    async def run():
        sandbox = await Sandbox.create("work")
        assert (await sandbox.exec("echo", ["hello"])).stdout == b"hello"
        await sandbox.write_file("/work/binary", b"\x00\x01\x02")
        assert await sandbox.read_file("/work/binary") == b"\x00\x01\x02"
        assert StateStore().require("work").microvm_id == "sb-one"
        await sandbox.suspend()
        await sandbox.start()
        await sandbox.terminate()
        assert StateStore().all() == []

    asyncio.run(run())


def test_cloud_rejects_aws_capabilities_before_launch(cloud):
    for extra in (
        {"execution_role_arn": "arn:aws:iam::123:role/admin"},
        {"egress_network_connectors": ["connector"]},
    ):
        with pytest.raises(SandboxError, match="self-hosted"):
            asyncio.run(Sandbox.create("work", **extra))
    assert not cloud[0]


def test_cli_and_observability_need_no_aws(cloud):
    runner = CliRunner()
    for args in (["create", "work"], ["ls"], ["logs", "work"], ["metrics", "work"], ["whoami"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.output, result.exception)
    result = runner.invoke(app, ["infra", "up"])
    assert result.exit_code == 1
    assert "managed by VMNest Cloud" in result.output


def test_saved_login_is_not_sent_to_another_backend(monkeypatch, tmp_path):
    path = tmp_path / "auth.json"
    monkeypatch.setenv("VMNEST_CONFIG", str(path))
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.delenv("VMNEST_API_TOKEN", raising=False)
    monkeypatch.delenv("VMNEST_API_URL", raising=False)
    save_login("https://one.example", "secret-token", "account-one")
    assert credentials() == ("https://one.example", "secret-token")
    if os.name == "nt":
        assert "secret-token" not in path.read_text()
    else:
        assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv("VMNEST_API_URL", "https://two.example")
    with pytest.raises(ControlPlaneError, match="Sign in"):
        credentials()
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    assert credentials() is None


def test_environment_token_does_not_inherit_a_saved_custom_origin(monkeypatch, tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("invalid stale credentials")
    monkeypatch.setenv("VMNEST_CONFIG", str(path))
    monkeypatch.setenv("VMNEST_API_TOKEN", "automation-token")
    monkeypatch.delenv("VMNEST_API_URL", raising=False)
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    assert credentials() == ("https://vmnest.cc", "automation-token")


def test_explicit_idle_policy_is_sent_to_hosted_service(cloud, monkeypatch):
    from agent_sandbox.models import IdlePolicy

    payloads = []
    original = HostedClient.request

    def request(self, method, path, **kwargs):
        if path == "/v1/sandboxes" and method == "POST":
            payloads.append(kwargs["json"])
            return cloud[1]
        return original(self, method, path, **kwargs)

    monkeypatch.setattr(HostedClient, "request", request)

    async def run():
        sandbox = await Sandbox.create("work", idle_policy=IdlePolicy(60, 120, True))
        await sandbox.terminate()

    asyncio.run(run())
    assert payloads[0]["idle_policy"] == {
        "max_idle_duration_seconds": 60,
        "suspended_duration_seconds": 120,
        "auto_resume_enabled": True,
    }


def test_suspend_waits_for_observed_state_before_allowing_resume(cloud, monkeypatch):
    client = HostedClient()
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        return {"state": "running" if method == "POST" else "suspended"}

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr("agent_sandbox.hosted.time.sleep", lambda _: None)
    assert client.suspend_microvm("sandbox-one")["state"] == "suspended"
    assert calls == [
        ("POST", "/v1/sandboxes/sandbox-one/state"),
        ("GET", "/v1/sandboxes/sandbox-one"),
    ]


def test_hosted_list_paginates_and_never_uses_local_registry(cloud, monkeypatch, tmp_path):
    local = tmp_path / "state.json"
    local.write_text('{"sandboxes":{"private":{"name":"private","microvm_id":"aws-private"}}}')
    monkeypatch.setenv("AGENT_SANDBOX_STATE", str(local))
    assert [entry.name for entry in StateStore().all()] == ["work"]
    client = HostedClient()
    calls = []

    def request(method, path, params):
        calls.append(dict(params))
        return [cloud[1]] * (100 if params["offset"] == 0 else 2)

    monkeypatch.setattr(client, "request", request)
    assert len(client.list("sandboxes")) == 102
    assert calls == [{"limit": 100, "offset": 0}, {"limit": 100, "offset": 100}]


def test_browser_login_polls_without_printing_or_persisting_plaintext(monkeypatch, tmp_path):
    import agent_sandbox.cli.auth as auth

    path = tmp_path / "auth.json"
    monkeypatch.setenv("VMNEST_CONFIG", str(path))
    monkeypatch.delenv("VMNEST_API_TOKEN", raising=False)
    monkeypatch.delenv("VMNEST_API_URL", raising=False)
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    requests = []

    def request(self, method, route, **kwargs):
        requests.append(route)
        if route == "/v1/auth/device":
            return {
                "device_code": "private-device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://cloud.example/device",
                "expires_in": 600,
                "interval": 1,
            }
        if requests.count(route) == 1:
            return None
        return {"token": "private-access-secret", "account_id": "account-one"}

    monkeypatch.setattr(HostedClient, "request", request)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", opened.append)
    result = CliRunner().invoke(app, ["login", "--api-url", "https://cloud.example"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "ABCD-EFGH" in result.output
    assert "private-" not in result.output
    assert opened == ["https://cloud.example/device"]
    assert credentials() == ("https://cloud.example", "private-access-secret")


def test_mcp_lifecycle_uses_the_hosted_sdk(cloud):
    from agent_sandbox_mcp.config import load_config
    from agent_sandbox_mcp.session import SandboxRegistry
    from agent_sandbox_mcp.tools.infra import build_outputs
    from agent_sandbox_mcp.tools.runtime import _check

    load_config.cache_clear()
    config = load_config()
    assert _check(config)["mode"] == "hosted"
    assert build_outputs(config)["managed"] is True

    async def run():
        registry = SandboxRegistry(config)
        sandbox = await registry.create_named("work")
        assert (await sandbox.exec("echo", ["hello"])).stdout_text == "hello"
        assert [row.name for row in registry.records()] == ["work"]
        await sandbox.terminate()
        await registry.aclose()

    asyncio.run(run())
    load_config.cache_clear()


def test_guest_upload_never_sends_account_bearer_to_storage(monkeypatch, tmp_path):
    source = tmp_path / "guest"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM example\n")
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "storage.example":
            assert "authorization" not in request.headers
            assert b"guest.zip" in request.content
            return httpx.Response(204)
        assert request.headers["authorization"] == "Bearer private-token"
        if request.url.path.endswith("/uploads"):
            return httpx.Response(
                200,
                json={
                    "upload_id": "upload-one",
                    "url": "https://storage.example",
                    "fields": {"key": "tenant-one/code.zip"},
                },
            )
        return httpx.Response(202, json={"id": "image-one", "state": "BUILDING"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    result = HostedClient("https://cloud.example", "private-token").build_image("python", source)
    assert result["id"] == "image-one"
    assert len(requests) == 3
