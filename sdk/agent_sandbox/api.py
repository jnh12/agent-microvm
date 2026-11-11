"""Local, account-free HTTP adapter over the shared VM backend."""

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backend import from_env
from .errors import SandboxError
from .ports import HOOK_PORT


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Body):
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,62}$")


class Exec(Body):
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
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

    @asynccontextmanager
    async def lifespan(app):
        from .recovery import exclusive_database

        interval = float(os.environ.get("VMNEST_RECOVERY_INTERVAL_SECONDS", "5"))
        if not 0 < interval < float("inf"):
            raise ValueError("Recovery interval must be finite and positive")

        async def worker():
            while True:
                await asyncio.sleep(interval)
                try:
                    await backend.recover()
                except Exception:
                    logging.getLogger(__name__).warning("Recovery check failed; will retry")

        with exclusive_database(backend.database):
            await backend.recover()
            task = asyncio.create_task(worker())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="VMNest single-user API", lifespan=lifespan)
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
        except SandboxError as exc:
            message = str(exc)
            if message.startswith(("REQUEST_CONFLICT:", "OUTCOME_UNKNOWN:", "LAUNCH_UNCERTAIN:")):
                return JSONResponse({"error": message}, status_code=409)
            return JSONResponse(
                {"error": "Operation failed; outcome may be uncertain"}, status_code=502
            )
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
        return await backend.create(body.name, request_key=body.request_key)

    @app.post("/v1/run")
    async def run(body: Exec):
        return await backend.run(**body.model_dump())

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
        token = await backend.plane(backend.record(name)).create_auth_token(
            record["id"], allowed_ports=[{"port": port}]
        )
        return {"endpoint": record["endpoint"], "token": token}

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8767, access_log=False)
