"""Filesystem tools for sandboxes.

``fs_read`` / ``fs_write`` use the ``agentd`` filesystem endpoints directly; the
rest (list, mkdir, remove, copy, rename, stat, exists) run small helpers inside
the VM. ``copy_from_host`` / ``copy_to_host`` bridge the host filesystem and are
gated by the host-path allowlist policy.
"""

from __future__ import annotations

import base64
import json
import os
from typing import TYPE_CHECKING

from agent_sandbox_mcp.config import Config
from agent_sandbox_mcp.envelope import cap_text, err, ok, tool_handler

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agent_sandbox_mcp.session import SandboxRegistry

# Helpers executed inside the VM (path passed as argv[1] to avoid quoting bugs).
_LIST_PY = (
    "import os,json,sys\n"
    "d=sys.argv[1]\n"
    "out=[]\n"
    "for n in sorted(os.listdir(d)):\n"
    " fp=os.path.join(d,n)\n"
    " try:\n"
    "  st=os.lstat(fp)\n"
    "  out.append({'name':n,'is_dir':os.path.isdir(fp),'is_symlink':os.path.islink(fp),"
    "'size':st.st_size,'mode':oct(st.st_mode),'mtime':int(st.st_mtime)})\n"
    " except OSError as e:\n"
    "  out.append({'name':n,'error':str(e)})\n"
    "print(json.dumps(out))\n"
)

_STAT_PY = (
    "import os,json,sys\n"
    "p=sys.argv[1]\n"
    "st=os.lstat(p)\n"
    "print(json.dumps({'path':p,'size':st.st_size,'mode':oct(st.st_mode),"
    "'is_dir':os.path.isdir(p),'is_file':os.path.isfile(p),'is_symlink':os.path.islink(p),"
    "'mtime':int(st.st_mtime),'uid':st.st_uid,'gid':st.st_gid}))\n"
)


def _abs(config: Config, path: str) -> str:
    if path.startswith("/"):
        return path
    return f"{config.workdir.rstrip('/')}/{path}"


def register(mcp: FastMCP, registry: SandboxRegistry, config: Config) -> None:
    async def _run(name: str, command: str, args: list[str]):
        sandbox = await registry.get(name)
        return await sandbox.exec(command, args, timeout=config.default_timeout_seconds)

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_read(name: str, path: str, encoding: str = "text") -> dict:
        """Read a sandbox file as UTF-8 `text` (default) or `base64` bytes."""
        sandbox = await registry.get(name)
        data = await sandbox.read_file(_abs(config, path))
        if encoding == "base64":
            content = base64.b64encode(data).decode("ascii")
            return ok({"path": _abs(config, path), "encoding": "base64",
                       "content": cap_text(content, config.max_output_bytes)})
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            return ok({"path": _abs(config, path), "encoding": "base64",
                       "content": cap_text(content, config.max_output_bytes)})
        return ok({"path": _abs(config, path), "encoding": "text",
                   "content": cap_text(text, config.max_output_bytes)})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_write(
        name: str, path: str, content: str, encoding: str = "text"
    ) -> dict:
        """Write `text` (UTF-8, default) or `base64` bytes to a sandbox file (creates parents)."""
        sandbox = await registry.get(name)
        raw = base64.b64decode(content) if encoding == "base64" else content.encode("utf-8")
        await sandbox.write_file(_abs(config, path), raw)
        return ok({"path": _abs(config, path), "bytes_written": len(raw)})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_list(name: str, path: str | None = None) -> dict:
        """List directory entries in a sandbox (defaults to the workdir)."""
        target = _abs(config, path) if path else config.workdir
        result = await _run(name, "python3", ["-c", _LIST_PY, target])
        if not result.success:
            return err(result.stderr_text.strip() or "list failed", code="fs_error")
        return ok({"path": target, "entries": json.loads(result.stdout_text or "[]")})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_mkdir(name: str, path: str) -> dict:
        """Create a directory (and parents) in a sandbox."""
        result = await _run(name, "mkdir", ["-p", _abs(config, path)])
        if not result.success:
            return err(result.stderr_text.strip() or "mkdir failed", code="fs_error")
        return ok({"path": _abs(config, path), "created": True})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_remove(name: str, path: str, recursive: bool = False) -> dict:
        """Remove a sandbox file or directory (`recursive` for non-empty dirs)."""
        args = ["-rf" if recursive else "-f", _abs(config, path)]
        result = await _run(name, "rm", args)
        if not result.success:
            return err(result.stderr_text.strip() or "remove failed", code="fs_error")
        return ok({"path": _abs(config, path), "removed": True})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_copy(name: str, src: str, dst: str) -> dict:
        """Copy a file or directory within a sandbox."""
        result = await _run(name, "cp", ["-r", _abs(config, src), _abs(config, dst)])
        if not result.success:
            return err(result.stderr_text.strip() or "copy failed", code="fs_error")
        return ok({"src": _abs(config, src), "dst": _abs(config, dst)})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_rename(name: str, src: str, dst: str) -> dict:
        """Rename/move a file or directory within a sandbox."""
        result = await _run(name, "mv", [_abs(config, src), _abs(config, dst)])
        if not result.success:
            return err(result.stderr_text.strip() or "rename failed", code="fs_error")
        return ok({"src": _abs(config, src), "dst": _abs(config, dst)})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_stat(name: str, path: str) -> dict:
        """Get metadata (size, mode, mtime, type) for a sandbox path."""
        result = await _run(name, "python3", ["-c", _STAT_PY, _abs(config, path)])
        if not result.success:
            return err(result.stderr_text.strip() or "stat failed", code="fs_error")
        return ok(json.loads(result.stdout_text))

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_exists(name: str, path: str) -> dict:
        """Check whether a sandbox path exists."""
        result = await _run(
            name,
            "python3",
            ["-c", "import os,sys;print('1' if os.path.exists(sys.argv[1]) else '0')",
             _abs(config, path)],
        )
        exists = result.success and result.stdout_text.strip() == "1"
        return ok({"path": _abs(config, path), "exists": exists})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_copy_from_host(name: str, host_path: str, dest: str) -> dict:
        """Copy an allowlisted host file into a sandbox."""
        if not config.host_path_allowed(host_path):
            return err(
                f"host path not allowed by policy: {host_path}", code="policy_denied"
            )
        if not os.path.isfile(host_path):
            return err(f"not a file: {host_path}", code="not_found")
        with open(host_path, "rb") as fh:
            data = fh.read()
        sandbox = await registry.get(name)
        await sandbox.write_file(_abs(config, dest), data)
        return ok({"host_path": host_path, "dest": _abs(config, dest),
                   "bytes_written": len(data)})

    @mcp.tool()
    @tool_handler
    async def sandbox_fs_copy_to_host(name: str, src: str, host_path: str) -> dict:
        """Copy a sandbox file to an allowlisted host destination."""
        if not config.host_path_allowed(host_path):
            return err(
                f"host path not allowed by policy: {host_path}", code="policy_denied"
            )
        sandbox = await registry.get(name)
        data = await sandbox.read_file(_abs(config, src))
        parent = os.path.dirname(os.path.abspath(host_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(host_path, "wb") as fh:
            fh.write(data)
        return ok({"src": _abs(config, src), "host_path": host_path,
                   "bytes_written": len(data)})
