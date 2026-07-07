"""JSON envelope + output-capping helpers shared by every tool.

Every tool returns ``{"ok": True, "data": ...}`` on success or
``{"ok": False, "error": {...}}`` on failure, matching microsandbox-mcp. Large
text (command output, logs, file reads) is capped and annotated with truncation
metadata so agents can tell when output was shortened.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def err(message: str, *, code: str = "error", **extra: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    return {"ok": False, "error": error}


def cap_text(text: str, max_bytes: int) -> dict[str, Any]:
    """Cap ``text`` to ``max_bytes`` (UTF-8) with truncation metadata.

    Returns a dict with ``text``, ``truncated`` (bool), and, when truncated, the
    ``total_bytes`` / ``returned_bytes`` so callers know output was shortened.
    """
    raw = text.encode("utf-8")
    if max_bytes <= 0 or len(raw) <= max_bytes:
        return {"text": text, "truncated": False}
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return {
        "text": clipped,
        "truncated": True,
        "total_bytes": len(raw),
        "returned_bytes": len(clipped.encode("utf-8")),
    }


def exec_result(result: Any, max_bytes: int) -> dict[str, Any]:
    """Normalize an :class:`agent_sandbox.ExecResult` into a capped payload."""
    return {
        "exit_code": result.exit_code,
        "success": result.success,
        "stdout": cap_text(result.stdout_text, max_bytes),
        "stderr": cap_text(result.stderr_text, max_bytes),
    }


def tool_handler(fn):
    """Wrap a tool coroutine so it always returns an envelope.

    Any raised exception becomes ``err(...)`` with the exception class name as
    the error ``code``, so a single tool failure never crashes the server.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001 - tools must never raise out
            return err(str(exc) or exc.__class__.__name__, code=exc.__class__.__name__)

    return wrapper
