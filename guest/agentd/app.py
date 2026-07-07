"""FastAPI application for the guest agent.

Auth note: authentication is handled by the Lambda MicroVMs ingress proxy, which
validates the ``X-aws-proxy-auth`` token before traffic reaches this process.
agentd therefore trusts inbound requests and does not re-validate the token.
"""

from __future__ import annotations

import base64
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agentd.exec import run_command

app = FastAPI(title="agentd", version="0.1.0")


class ExecRequest(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: float | None = None


class ExecResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    encoding: str = "base64"


class ReadRequest(BaseModel):
    path: str


class ReadResponse(BaseModel):
    content: str
    encoding: str = "base64"


class WriteRequest(BaseModel):
    path: str
    content: str
    encoding: str = "base64"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.post("/v1/exec", response_model=ExecResponse)
async def exec_command(req: ExecRequest) -> ExecResponse:
    try:
        outcome = await run_command(
            req.command,
            req.args,
            cwd=req.cwd,
            env=req.env,
            timeout=req.timeout,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="command timed out") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"command not found: {req.command}") from exc
    return ExecResponse(
        exit_code=outcome.exit_code,
        stdout=base64.b64encode(outcome.stdout).decode("ascii"),
        stderr=base64.b64encode(outcome.stderr).decode("ascii"),
    )


@app.post("/v1/fs/read", response_model=ReadResponse)
async def read_file(req: ReadRequest) -> ReadResponse:
    try:
        with open(req.path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no such file: {req.path}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReadResponse(content=base64.b64encode(data).decode("ascii"))


@app.post("/v1/fs/write")
async def write_file(req: WriteRequest) -> dict[str, int]:
    if req.encoding == "base64":
        try:
            data = base64.b64decode(req.content)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid base64 content") from exc
    else:
        data = req.content.encode("utf-8")

    directory = os.path.dirname(req.path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        with open(req.path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"bytes_written": len(data)}
