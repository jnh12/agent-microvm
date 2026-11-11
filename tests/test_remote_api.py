from types import SimpleNamespace

import pytest
from agent_sandbox.api import create_app
from agent_sandbox.backend import Backend
from agent_sandbox.cli.main import app
from agent_sandbox.control_plane import MicroVMInfo
from agent_sandbox.models import ExecResult
from fastapi.testclient import TestClient
from typer.testing import CliRunner


@pytest.fixture
def remote(tmp_path, monkeypatch):
    vms, files, commands, tokens = {}, {}, [], []

    class CP:
        async def run_microvm(
            self, image_arn, execution_role_arn=None, idle_policy=None, client_token=None
        ):
            assert image_arn == "fixed-image" and execution_role_arn == "fixed-role"
            identifier = str(len(vms) + 1)
            vms[identifier] = "RUNNING"
            return await self.get_microvm(identifier)

        async def get_microvm(self, identifier):
            return MicroVMInfo(identifier, vms[identifier], "guest.test", {})

        async def wait_until_running(self, identifier):
            return await self.get_microvm(identifier)

        async def create_auth_token(self, identifier, allowed_ports=None):
            tokens.append((identifier, allowed_ports))
            return "scoped-test-token"

    class VM:
        _agent = None

        @classmethod
        async def attach(cls, name, identifier, region=None):
            vm = cls()
            vm.id = identifier
            return vm

        async def exec(self, command, args, **kwargs):
            commands.append((command, args, kwargs))
            if command == "fail-transport":
                raise TimeoutError()
            return ExecResult(7, b"out", b"err")

        async def suspend(self):
            vms[self.id] = "SUSPENDED"

        async def start(self):
            vms[self.id] = "RUNNING"

        async def terminate(self):
            vms[self.id] = "TERMINATED"

        async def read_file(self, path):
            return files[path]

        async def write_file(self, path, content):
            files[path] = content

    backend = Backend(
        str(tmp_path / "state.db"),
        "fixed-image",
        "fixed-role",
        "test",
        sandbox=VM,
        control_plane=CP(),
    )
    api = TestClient(create_app(backend), base_url="http://127.0.0.1")

    def request(method, path, body=None):
        response = api.request(method, path, json=body)
        if response.is_error:
            from agent_sandbox.errors import SandboxError

            raise SandboxError(response.text)
        return response.json()

    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    monkeypatch.setattr("agent_sandbox.remote.request", request)
    return SimpleNamespace(
        api=api,
        backend=backend,
        vms=vms,
        files=files,
        commands=commands,
        tokens=tokens,
        runner=CliRunner(),
    )


def test_remote_cli_lifecycle_and_server_inventory(remote, monkeypatch):
    from agent_sandbox.cli.state import StateStore

    monkeypatch.setattr(StateStore, "__init__", lambda *a, **k: pytest.fail("local state accessed"))
    for args in [
        ["create", "demo"],
        ["ls"],
        ["inspect", "demo"],
        ["stop", "demo"],
        ["ps", "demo"],
        ["start", "demo"],
        ["rm", "demo"],
    ]:
        result = remote.runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert remote.vms == {"1": "TERMINATED"}
    assert remote.api.get("/v1/vms").json() == {"items": []}


def test_exec_and_run_preserve_exit_and_cleanup(remote):
    remote.api.post("/v1/vms", json={"name": "demo"})
    result = remote.runner.invoke(app, ["exec", "demo", "--", "python", "-c", "hello world"])
    assert result.exit_code == 7
    assert result.stdout == "out" and result.stderr == "err"
    assert remote.commands[-1][:2] == ("python", ["-c", "hello world"])
    result = remote.runner.invoke(app, ["run", "default", "--", "fail-transport"])
    assert result.exit_code == 1
    assert remote.vms["2"] == "TERMINATED"
    assert len(remote.api.get("/v1/vms").json()["items"]) == 1


def test_file_round_trip_and_forward_scope(remote, tmp_path):
    from agent_sandbox.ports import HOOK_PORT

    remote.api.post("/v1/vms", json={"name": "demo"})
    source, dest = tmp_path / "source", tmp_path / "dest"
    source.write_bytes(bytes(range(256)))
    assert remote.runner.invoke(app, ["upload", "demo", str(source), "/tmp/data"]).exit_code == 0
    assert remote.runner.invoke(app, ["download", "demo", "/tmp/data", str(dest)]).exit_code == 0
    assert dest.read_bytes() == source.read_bytes()
    assert remote.api.post(f"/v1/vms/demo/forward/{HOOK_PORT}").status_code == 403
    assert not remote.tokens
    assert remote.api.post("/v1/vms/demo/forward/8081").json()["token"] == "scoped-test-token"
    assert remote.tokens == [("1", [{"port": 8081}])]
    assert remote.api.post("/v1/vms/missing/forward/8081").status_code == 404


def test_reject_admin_fields_browser_requests_and_duplicates(remote):
    api = remote.api
    assert api.post("/v1/vms", json={"name": "demo", "role": "arbitrary"}).status_code == 422
    assert (
        api.post(
            "/v1/vms", json={"name": "demo"}, headers={"Origin": "https://untrusted.test"}
        ).status_code
        == 403
    )
    assert api.get("/healthz", headers={"Host": "untrusted.test"}).status_code == 400
    assert api.post("/v1/vms", json={"name": "demo"}).status_code == 200
    assert api.post("/v1/vms", json={"name": "demo"}).status_code == 409
    assert len(remote.vms) == 1
    for args in [["infra", "up"], ["image", "ls"], ["create", "bad", "--role", "arbitrary"]]:
        assert remote.runner.invoke(app, args).exit_code != 0
    assert not any(
        "infra" in path or "auth" in path for path in api.get("/openapi.json").json()["paths"]
    )


def test_restart_keeps_inventory(remote):
    remote.api.post("/v1/vms", json={"name": "demo"})
    previous = remote.backend
    restarted = Backend(
        previous.database,
        previous.image,
        previous.role,
        previous.region,
        sandbox=previous.sandbox,
        control_plane=previous.cp,
    )
    api = TestClient(create_app(restarted), base_url="http://127.0.0.1")
    assert api.get("/v1/vms/demo").json()["id"] == "1"


def test_run_cleans_up_after_readiness_failure(remote, monkeypatch):
    async def fail(identifier):
        raise TimeoutError()

    monkeypatch.setattr(remote.backend.cp, "wait_until_running", fail)
    response = remote.api.post("/v1/run", json={"command": "python", "args": ["-V"]})
    assert response.status_code == 502
    assert remote.vms == {"1": "TERMINATED"}
    assert remote.api.get("/v1/vms").json()["items"] == []


def test_logs_and_metrics_use_record_identity(remote, monkeypatch):
    import agent_sandbox.observability as observation

    calls = []

    def logs(identifier, region):
        calls.append((identifier, region))
        return [{"message": "guest log"}]

    def metrics(identifier, region):
        calls.append((identifier, region))
        return {"CPUUtilization": 12.5}

    monkeypatch.setattr(observation, "read_logs", logs)
    monkeypatch.setattr(observation, "read_metrics", metrics)
    remote.api.post("/v1/vms", json={"name": "demo"})
    assert "guest log" in remote.runner.invoke(app, ["logs", "demo"]).output
    assert "12.5" in remote.runner.invoke(app, ["metrics", "demo"]).output
    assert calls == [("1", "test"), ("1", "test")]
    assert remote.api.get("/v1/vms/missing/logs").status_code == 404
    assert len(calls) == 2


def test_create_replay_and_tombstone_never_launch_again(remote):
    api = remote.api
    body = {"name": "demo", "request_key": "create-one"}
    first = api.post("/v1/vms", json=body).json()
    assert api.post("/v1/vms", json=body).json()["id"] == first["id"]
    assert api.post("/v1/vms", json={**body, "name": "other"}).status_code == 409
    api.delete("/v1/vms/demo")
    assert api.post("/v1/vms", json=body).json()["state"] == "TERMINATED"
    second = api.post("/v1/vms", json={**body, "request_key": "create-two"}).json()
    assert second["id"] != first["id"]
    assert api.post("/v1/vms", json=body).json()["id"] == first["id"]
    assert len(remote.vms) == 2


def test_execution_replay_and_uncertainty_never_repeat_command(remote):
    remote.api.post("/v1/vms", json={"name": "demo"})
    for endpoint in ("/v1/vms/demo/exec", "/v1/run"):
        body = {"command": "python", "request_key": endpoint}
        result = remote.api.post(endpoint, json=body)
        assert result.status_code == 200
        before = len(remote.commands)
        assert remote.api.post(endpoint, json=body).json() == result.json()
        assert len(remote.commands) == before
        assert remote.api.post(endpoint, json={**body, "command": "other"}).status_code == 409
        body = {"command": "fail-transport", "request_key": endpoint + "-failure"}
        assert remote.api.post(endpoint, json=body).status_code == 502
        before = len(remote.commands)
        assert remote.api.post(endpoint, json=body).status_code == 409
        assert len(remote.commands) == before


def test_restart_cleans_interrupted_run_and_retries_failed_delete(remote, monkeypatch):
    import asyncio

    remote.api.post("/v1/vms", json={"name": "ephemeral"})
    remote.api.post("/v1/vms", json={"name": "deleting"})
    original = remote.backend.sandbox.terminate

    async def fail(self):
        raise TimeoutError()

    monkeypatch.setattr(remote.backend.sandbox, "terminate", fail)
    assert remote.api.delete("/v1/vms/deleting").status_code == 502
    assert remote.backend.record("deleting")["desired"] == "TERMINATED"
    monkeypatch.setattr(remote.backend.sandbox, "terminate", original)
    with remote.backend.db() as db:
        db.execute("UPDATE vms SET ephemeral=1 WHERE name='ephemeral'")
    old = remote.backend
    restarted = Backend(
        old.database, old.image, old.role, old.region, sandbox=old.sandbox, control_plane=old.cp
    )
    asyncio.run(restarted.recover())
    assert set(remote.vms.values()) == {"TERMINATED"}
    assert asyncio.run(restarted.list()) == []


def test_lost_launch_id_remains_unknown_without_relaunch(remote, monkeypatch):
    import asyncio

    original = remote.backend.cp.run_microvm
    calls = []

    async def lose_response(**kwargs):
        calls.append(kwargs)
        await original(**kwargs)
        raise TimeoutError()

    monkeypatch.setattr(remote.backend.cp, "run_microvm", lose_response)
    assert (
        remote.api.post("/v1/vms", json={"name": "demo", "request_key": "lost"}).status_code == 502
    )
    import json

    assert (
        calls[0]["client_token"]
        == json.loads(remote.backend.record("demo")["payload"])["client_token"]
    )
    asyncio.run(remote.backend.recover())
    assert remote.backend.record("demo")["state"] == "UNKNOWN"
    assert len(calls) == 1 and len(remote.vms) == 1
    assert (
        remote.api.post("/v1/vms", json={"name": "demo", "request_key": "lost"}).json()["state"]
        == "UNKNOWN"
    )
    assert remote.api.post("/v1/vms", json={"name": "demo"}).status_code == 409


def test_active_run_is_not_cleaned_up_by_recovery(remote, monkeypatch):
    import asyncio

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = remote.backend.sandbox.exec

        async def block(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(remote.backend.sandbox, "exec", block)
        task = asyncio.create_task(
            remote.backend.run(command="python", args=[], request_key="active")
        )
        await entered.wait()
        await remote.backend.recover()
        assert set(remote.vms.values()) == {"RUNNING"}
        release.set()
        await task
        assert set(remote.vms.values()) == {"TERMINATED"}

    asyncio.run(scenario())


def test_exclusive_database_lock_and_legacy_migration(tmp_path):
    import sqlite3

    from agent_sandbox.recovery import exclusive_database

    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE vms (name TEXT PRIMARY KEY,id TEXT,state TEXT)")
        db.execute("INSERT INTO vms VALUES ('old', NULL, 'CREATING')")
    backend = Backend(path, "image", "role", "region", control_plane=object())
    assert backend.record("old")["started"] == 1
    with exclusive_database(path):
        with pytest.raises(RuntimeError, match="Another backend"):
            with exclusive_database(path):
                pass
    with exclusive_database(path):
        pass


def test_unstarted_reservation_recovers_original_configuration(remote):
    import asyncio
    import json

    backend = remote.backend
    backend.begin_operation("reserved", "create", {"name": "queued"})
    payload = {
        "image_arn": "fixed-image",
        "execution_role_arn": "fixed-role",
        "idle_policy": {},
        "client_token": "persisted-token",
    }
    with backend.db() as db:
        db.execute(
            "INSERT INTO vms (name,state,generation,payload,region) "
            "VALUES ('queued','CREATING','reserved',?,'test')",
            (json.dumps(payload),),
        )
    backend.image, backend.role = "changed-image", "changed-role"
    asyncio.run(backend.recover())
    assert backend.record("queued")["id"] == "1"
    assert (
        remote.api.post("/v1/vms", json={"name": "queued", "request_key": "reserved"}).json()["id"]
        == "1"
    )
    assert len(remote.vms) == 1
