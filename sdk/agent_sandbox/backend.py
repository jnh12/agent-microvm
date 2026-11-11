"""Single-user VM operations shared by the HTTP service.

Provisioning is deliberately absent. VM implementations belong to the existing SDK.
"""

import asyncio
import base64
import os
import sqlite3
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
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS vms (name TEXT PRIMARY KEY, id TEXT, state TEXT)"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, name):
        with self.db() as db:
            row = db.execute("SELECT * FROM vms WHERE name=?", (name,)).fetchone()
        if not row:
            raise KeyError(name)
        return dict(row)

    async def create(self, name):
        with self.db() as db:
            db.execute("INSERT INTO vms VALUES (?, NULL, 'CREATING')", (name,))
        # Persist identity before waiting. An interrupted launch stays reserved for inspection.
        info = await self.cp.run_microvm(
            self.image, execution_role_arn=self.role, idle_policy=IdlePolicy().to_aws()
        )
        with self.db() as db:
            db.execute(
                "UPDATE vms SET id=?, state=? WHERE name=?", (info.microvm_id, info.status, name)
            )
        await self.cp.wait_until_running(info.microvm_id)
        return await self.inspect(name)

    async def inspect(self, name):
        row = self.record(name)
        if not row["id"]:
            return row
        info = await self.cp.get_microvm(row["id"])
        return {
            "name": name,
            "id": info.microvm_id,
            "state": info.status,
            "endpoint": info.endpoint,
            "region": self.region,
        }

    async def list(self):
        with self.db() as db:
            names = [row[0] for row in db.execute("SELECT name FROM vms ORDER BY name")]
        return [await self.inspect(name) for name in names]

    @asynccontextmanager
    async def attached(self, name):
        row = self.record(name)
        if not row["id"]:
            raise SandboxError("VM creation is unresolved; inspect backend state")
        vm = await self.sandbox.attach(name, row["id"], region=self.region)
        try:
            yield vm
        finally:
            # Close only the transport, never the VM (Sandbox.__aexit__ terminates it).
            if vm._agent is not None:
                await vm._agent.aclose()

    async def lifecycle(self, name, action):
        async with self.attached(name) as vm:
            await getattr(
                vm, {"suspend": "suspend", "resume": "start", "terminate": "terminate"}[action]
            )()
        if action == "terminate":
            with self.db() as db:
                db.execute("DELETE FROM vms WHERE name=?", (name,))
            return {"name": name, "state": "TERMINATED"}
        return await self.inspect(name)

    async def execute(self, name, command, args, cwd=None, env=None, timeout=None):
        async with self.attached(name) as vm:
            result = await vm.exec(command, args, cwd=cwd, env=env, timeout=timeout)
        return {
            "exit_code": result.exit_code,
            "encoding": "base64",
            "stdout": base64.b64encode(result.stdout).decode(),
            "stderr": base64.b64encode(result.stderr).decode(),
        }

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

        return await asyncio.to_thread(read_logs, self.record(name)["id"], self.region)

    async def metrics(self, name):
        from .observability import read_metrics

        return await asyncio.to_thread(read_metrics, self.record(name)["id"], self.region)


def from_env():
    return Backend(
        os.environ.get("VMNEST_API_DB", "vmnest-api.db"),
        os.environ["AGENT_SANDBOX_IMAGE_ARN"],
        os.environ["AGENT_SANDBOX_EXECUTION_ROLE_ARN"],
        os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
    )
