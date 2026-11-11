"""Local, account-free HTTP adapter over the shared VM backend."""

import secrets
import sqlite3
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backend import from_env
from .ports import HOOK_PORT


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Body):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,62}$")


class Exec(Body):
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class File(Body):
    path: str


class Write(File):
    content: str


def create_app(backend=None):
    backend = backend or from_env()
    app = FastAPI(title="VMNest single-user API")
    app.state.backend = backend
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        # An unauthenticated localhost API must not accept browser-origin requests.
        if request.headers.get("origin"):
            return JSONResponse({"error": "Browser-origin requests are disabled"}, status_code=403)
        try:
            return await call_next(request)
        except KeyError:
            return JSONResponse({"error": "VM not found"}, status_code=404)
        except sqlite3.IntegrityError:
            return JSONResponse({"error": "VM name already reserved"}, status_code=409)
        except Exception:
            return JSONResponse(
                {"error": "Operation failed; outcome may be uncertain. No automatic replay."},
                status_code=502,
            )

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.post("/v1/vms")
    async def create(body: Create):
        return await backend.create(body.name)

    @app.post("/v1/run")
    async def run(body: Exec):
        name = "run-" + secrets.token_hex(8)
        try:
            await backend.create(name)
            return await backend.execute(name, **body.model_dump())
        finally:
            try:
                row = backend.record(name)
            except KeyError:
                row = None
            if row and row["id"]:
                await backend.lifecycle(name, "terminate")

    @app.get("/v1/vms")
    async def listing():
        return {"items": await backend.list()}

    @app.get("/v1/vms/{name}")
    async def inspect(name: str):
        return await backend.inspect(name)

    @app.delete("/v1/vms/{name}")
    async def terminate(name: str):
        return await backend.lifecycle(name, "terminate")

    @app.post("/v1/vms/{name}/suspend")
    async def suspend(name: str):
        return await backend.lifecycle(name, "suspend")

    @app.post("/v1/vms/{name}/resume")
    async def resume(name: str):
        return await backend.lifecycle(name, "resume")

    @app.post("/v1/vms/{name}/exec")
    async def execute(name: str, body: Exec):
        return await backend.execute(name, **body.model_dump())

    @app.post("/v1/vms/{name}/files/read")
    async def read(name: str, body: File):
        return await backend.read(name, body.path)

    @app.post("/v1/vms/{name}/files/write")
    async def write(name: str, body: Write):
        return await backend.write(name, body.path, body.content)

    @app.get("/v1/vms/{name}/logs")
    async def logs(name: str):
        return await backend.logs(name)

    @app.get("/v1/vms/{name}/metrics")
    async def metrics(name: str):
        return await backend.metrics(name)

    @app.post("/v1/vms/{name}/forward/{port}")
    async def forward(name: str, port: Annotated[int, Field(ge=1, le=65535)]):
        if port == HOOK_PORT:
            return JSONResponse({"error": "Reserved lifecycle port"}, status_code=403)
        record = await backend.inspect(name)
        token = await backend.cp.create_auth_token(record["id"], allowed_ports=[{"port": port}])
        return {"endpoint": record["endpoint"], "token": token}

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8767, access_log=False)
