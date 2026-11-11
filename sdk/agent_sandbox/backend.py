"""Single-user VM operations shared by the HTTP service.

Provisioning is deliberately absent. VM implementations belong to the existing SDK.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager

from .control_plane import ControlPlane
from .errors import SandboxError
from .models import IdlePolicy
from .sandbox import Sandbox


class Backend:
    def __init__(self, database, image, role, region, sandbox=Sandbox, control_plane=None):
        self.database, self.image, self.role, self.region = database, image, role, region
        self.sandbox = sandbox
        self.cp = control_plane or ControlPlane(region=region)
        self.active = set()
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS vms (name TEXT PRIMARY KEY, id TEXT, state TEXT)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(vms)")}
            for name, kind in (
                ("generation", "TEXT"),
                ("payload", "TEXT"),
                ("region", "TEXT"),
                ("started", "INTEGER DEFAULT 0"),
                ("desired", "TEXT DEFAULT 'RUNNING'"),
                ("ephemeral", "INTEGER DEFAULT 0"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE vms ADD COLUMN {name} {kind}")
            db.execute("UPDATE vms SET region=? WHERE region IS NULL", (region,))
            db.execute("UPDATE vms SET started=1 WHERE payload IS NULL AND id IS NULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations "
                "(key TEXT PRIMARY KEY, fingerprint TEXT, result TEXT)"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    def record(self, name):
        with self.db() as db:
            row = db.execute("SELECT * FROM vms WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(name)
        return dict(row)

    def begin_operation(self, key, action, body):
        fingerprint = hashlib.sha256(
            json.dumps([action, body], sort_keys=True).encode()
        ).hexdigest()
        with self.db() as db:
            row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise SandboxError("REQUEST_CONFLICT: request key was used for different input")
                if row["result"] is None:
                    raise SandboxError(
                        "OUTCOME_UNKNOWN: operation is pending or interrupted; not replayed"
                    )
                return json.loads(row["result"])
            db.execute("INSERT INTO operations VALUES (?, ?, NULL)", (key, fingerprint))
        return None

    def finish_operation(self, key, result):
        with self.db() as db:
            db.execute("UPDATE operations SET result=? WHERE key=?", (json.dumps(result), key))

    async def create(self, name, request_key=None, ephemeral=False):
        key = request_key or str(uuid.uuid4())
        with self.db() as db:
            row = db.execute("SELECT * FROM vms WHERE name=?", (name,)).fetchone()
        if row and row["generation"] == key:
            return await self.inspect(name)
        replay = self.begin_operation(key, "create", {"name": name})
        if replay is not None:
            return replay
        payload = {
            "image_arn": self.image,
            "execution_role_arn": self.role,
            "idle_policy": IdlePolicy().to_aws(),
            "client_token": str(uuid.uuid4()),
        }
        with self.db() as db:
            db.execute("DELETE FROM vms WHERE name=? AND state='TERMINATED'", (name,))
            db.execute(
                "INSERT INTO vms (name,state,generation,payload,region,ephemeral) "
                "VALUES (?, 'CREATING', ?, ?, ?, ?)",
                (name, key, json.dumps(payload), self.region, int(ephemeral)),
            )
        self.active.add(name)
        try:
            await self.launch(name)
            row = self.record(name)
            await self.plane(row).wait_until_running(row["id"])
            result = await self.inspect(name)
            self.finish_operation(key, result)
            return result
        finally:
            self.active.discard(name)

    def plane(self, row):
        return self.cp if row["region"] == self.region else ControlPlane(region=row["region"])

    async def launch(self, name):
        row = self.record(name)
        if row["id"]:
            return
        if row["started"] or not row["payload"]:
            with self.db() as db:
                db.execute("UPDATE vms SET state='UNKNOWN' WHERE name=?", (name,))
            raise SandboxError("LAUNCH_UNCERTAIN: launch outcome needs manual reconciliation")
        with self.db() as db:
            db.execute("UPDATE vms SET started=1 WHERE name=?", (name,))
        info = await self.plane(row).run_microvm(**json.loads(row["payload"]))
        with self.db() as db:
            db.execute(
                "UPDATE vms SET id=?,state=? WHERE name=?", (info.microvm_id, info.status, name)
            )

    async def recover(self):
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM vms WHERE state!='TERMINATED'")]
        for row in rows:
            name = row["name"]
            if name in self.active:
                continue
            if row["state"] == "UNKNOWN" and not row["id"]:
                continue
            self.active.add(name)
            try:
                if row["ephemeral"] or row["desired"] == "TERMINATED":
                    if not row["id"] and not row["started"]:
                        with self.db() as db:
                            db.execute("UPDATE vms SET state='TERMINATED' WHERE name=?", (name,))
                    elif row["id"]:
                        await self.lifecycle(name, "terminate")
                    else:
                        await self.launch(name)  # Marks ambiguous launches UNKNOWN, never replays.
                elif row["state"] in {"CREATING", "PENDING", "UNKNOWN"}:
                    await self.launch(name)
                    result = await self.inspect(name)
                    if row["generation"]:
                        self.finish_operation(row["generation"], result)
            except Exception:
                logging.getLogger(__name__).warning("Recovery incomplete for VM %s", name)
            finally:
                self.active.discard(name)

    async def run(self, request_key=None, **command):
        key = request_key or str(uuid.uuid4())
        replay = self.begin_operation(key, "run", command)
        if replay is not None:
            return replay
        name = "run-" + uuid.uuid4().hex
        try:
            await self.create(name, ephemeral=True)
            self.active.add(name)
            result = await self.execute(name, **command)
            self.finish_operation(key, result)
            return result
        finally:
            self.active.discard(name)
            try:
                row = self.record(name)
            except KeyError:
                row = None
            if row:
                await self.lifecycle(name, "terminate")

    async def inspect(self, name):
        row = self.record(name)
        if not row["id"] or row["state"] == "TERMINATED":
            return {key: row[key] for key in ("name", "id", "state", "region")}
        info = await self.plane(row).get_microvm(row["id"])
        with self.db() as db:
            db.execute("UPDATE vms SET state=? WHERE name=?", (info.status, name))
        result = {
            "name": name,
            "id": info.microvm_id,
            "state": info.status,
            "endpoint": info.endpoint,
            "region": row["region"],
        }
        if info.status == "TERMINATED" and row["generation"]:
            self.finish_operation(row["generation"], result)
        return result

    async def list(self):
        with self.db() as db:
            names = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM vms WHERE state!='TERMINATED' ORDER BY name"
                )
            ]
        return [await self.inspect(name) for name in names]

    @asynccontextmanager
    async def attached(self, name, allow_terminating=False):
        row = self.record(name)
        if not allow_terminating and (
            row["desired"] == "TERMINATED" or row["state"] == "TERMINATED"
        ):
            raise SandboxError("VM is terminating or terminated")
        if not row["id"]:
            raise SandboxError("VM creation is unresolved; inspect backend state")
        vm = await self.sandbox.attach(name, row["id"], region=row["region"])
        try:
            yield vm
        finally:
            # Close only the transport, never the VM (Sandbox.__aexit__ terminates it).
            if vm._agent is not None:
                await vm._agent.aclose()

    async def lifecycle(self, name, action):
        row = self.record(name)
        if row["state"] == "TERMINATED":
            return await self.inspect(name)
        if action == "terminate":
            with self.db() as db:
                db.execute("UPDATE vms SET desired='TERMINATED' WHERE name=?", (name,))
            if not row["id"]:
                if row["started"]:
                    raise SandboxError("LAUNCH_UNCERTAIN: cannot terminate without confirmed VM ID")
                with self.db() as db:
                    db.execute("UPDATE vms SET state='TERMINATED' WHERE name=?", (name,))
                return await self.inspect(name)
        async with self.attached(name, allow_terminating=action == "terminate") as vm:
            await getattr(
                vm, {"suspend": "suspend", "resume": "start", "terminate": "terminate"}[action]
            )()
        result = await self.inspect(name)
        if result["state"] == "TERMINATED" and row["generation"]:
            self.finish_operation(row["generation"], result)
        return result

    async def execute(
        self, name, command, args, cwd=None, env=None, timeout=None, request_key=None
    ):
        if request_key:
            replay = self.begin_operation(
                request_key,
                "exec",
                {
                    "name": name,
                    "generation": self.record(name)["generation"],
                    "command": command,
                    "args": args,
                    "cwd": cwd,
                    "env": env,
                    "timeout": timeout,
                },
            )
            if replay is not None:
                return replay
        async with self.attached(name) as vm:
            result = await vm.exec(command, args, cwd=cwd, env=env, timeout=timeout)
        response = {
            "exit_code": result.exit_code,
            "encoding": "base64",
            "stdout": base64.b64encode(result.stdout).decode(),
            "stderr": base64.b64encode(result.stderr).decode(),
        }

        if request_key:
            self.finish_operation(request_key, response)
        return response

    async def read(self, name, path):
        async with self.attached(name) as vm:
            return {"content": base64.b64encode(await vm.read_file(path)).decode()}

    async def write(self, name, path, content):
        data = base64.b64decode(content, validate=True)
        async with self.attached(name) as vm:
            await vm.write_file(path, data)
        return {"ok": True}

    async def logs(self, name):
        from .observability import read_logs

        row = self.record(name)
        return await asyncio.to_thread(read_logs, row["id"], row["region"])

    async def metrics(self, name):
        from .observability import read_metrics

        row = self.record(name)
        return await asyncio.to_thread(read_metrics, row["id"], row["region"])


def from_env():
    return Backend(
        os.environ.get("VMNEST_API_DB", "vmnest-api.db"),
        os.environ["AGENT_SANDBOX_IMAGE_ARN"],
        os.environ["AGENT_SANDBOX_EXECUTION_ROLE_ARN"],
        os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
    )
