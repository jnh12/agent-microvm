"""Lifecycle-hook HTTP server, called by the AWS Lambda MicroVMs platform.

This is the *lifecycle plane*: a separate FastAPI app (served on ``HOOK_PORT`` by
``serve.py``) that the platform invokes at image build and MicroVM state
transitions. It is never reached by application clients — the ``forward`` CLI
guard keeps auth tokens from ever being scoped to the hook port.

Contract: return 200 on success; return 503 from ``/ready`` and ``/validate``
when the app needs more time (the platform keeps polling until the configured
timeout). Handlers are kept fast and idempotent per the platform guidance.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

_PYTHON = sys.executable or "python3"

from fastapi import FastAPI, Request, Response

from agentd import identity, state
from agentd.exec import run_command

log = logging.getLogger("agentd.hooks")

hooks_app = FastAPI(title="agentd-hooks", version="0.1.0")

# The platform fixes these paths; only the port is ours to choose.
PREFIX = "/aws/lambda-microvms/runtime/v1"

# Suspend drain budget: how long /suspend waits for in-flight exec to finish.
# Must stay under the suspendTimeoutInSeconds configured in the image's --hooks
# (see agent_sandbox.infra.resources._default_hooks) or the platform gives up
# first. Overridable so it can track that value without editing code.
_SUSPEND_DRAIN_SECONDS = float(os.getenv("AGENTD_SUSPEND_DRAIN_SECONDS", "8"))

# /run fires once per boot-from-snapshot; guard against duplicate delivery so a
# stray call can't rotate identity out from under a running workload.
_run_lock = threading.Lock()
_run_done = False


@hooks_app.post(f"{PREFIX}/ready")
async def ready() -> Response:
    """Build hook: 200 once agentd has started, else 503 so the platform retries."""
    return Response(status_code=200 if state.ready.is_set() else 503)


@hooks_app.post(f"{PREFIX}/validate")
async def validate() -> Response:
    """Build hook: smoke-test the snapshot by running a trivial exec in-process."""
    try:
        outcome = await run_command(_PYTHON, ["-c", "pass"], timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - any failure means "not valid yet"
        log.warning("validate: exec failed: %s", exc)
        return Response(status_code=503)
    return Response(status_code=200 if outcome.exit_code == 0 else 503)


@hooks_app.post(f"{PREFIX}/run")
async def run(request: Request) -> Response:
    """Runtime hook: fires once after run-from-snapshot. Mint per-VM identity."""
    global _run_done
    with _run_lock:
        if _run_done:
            return Response(status_code=200)  # idempotent: already ran this boot
    payload = await request.body()
    identity.regenerate("run", run_payload=payload or None)
    # Mark done only AFTER a successful regenerate, so a transient failure leaves
    # the flag clear and the platform's retry still gets a fresh identity.
    with _run_lock:
        _run_done = True
    return Response(status_code=200)


@hooks_app.post(f"{PREFIX}/resume")
async def resume() -> Response:
    """Runtime hook: fires after SUSPENDED -> RUNNING. Reseed randomness/identity."""
    identity.regenerate("resume")
    return Response(status_code=200)


@hooks_app.post(f"{PREFIX}/suspend")
async def suspend() -> Response:
    """Runtime hook: fires before RUNNING -> SUSPENDED. Drain in-flight exec."""
    state.wait_drain(_SUSPEND_DRAIN_SECONDS)
    return Response(status_code=200)


@hooks_app.post(f"{PREFIX}/terminate")
async def terminate() -> Response:
    """Runtime hook: fires before termination. Flush logs; best-effort cleanup."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - best-effort
            pass
    logging.shutdown()
    return Response(status_code=200)
