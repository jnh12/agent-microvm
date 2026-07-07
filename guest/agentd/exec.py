"""Command execution inside the guest.

Captures stdout, stderr, and the exit code. A dedicated module (mirroring
microsandbox's split between the agent loop and exec/session handling) so PTY
support can be added here later without touching the HTTP layer.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


@dataclass(slots=True)
class ExecOutcome:
    exit_code: int
    stdout: bytes
    stderr: bytes


async def run_command(
    command: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> ExecOutcome:
    """Run ``command`` with ``args`` and capture its output.

    ``env`` is merged onto the current process environment rather than replacing
    it, so PATH and friends remain intact.
    """
    argv = [command, *(args or [])]
    merged_env = {**os.environ, **(env or {})}

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=merged_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return ExecOutcome(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )
