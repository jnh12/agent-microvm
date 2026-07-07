"""Tests for image-agnostic working-directory bootstrap in SandboxRegistry."""

from __future__ import annotations

import asyncio

from agent_sandbox.cli.state import StateStore

from agent_sandbox_mcp.session import SandboxRegistry


class _Cfg:
    workdir = "/work"
    region = None
    verify_tls = True


class _ExecResult:
    def __init__(self, success=True, stderr_text=""):
        self.success = success
        self.stderr_text = stderr_text


class _FakeSandbox:
    def __init__(self, mkdir_ok=True):
        self.mkdir_ok = mkdir_ok
        self.exec_calls: list[tuple[str, list | None]] = []
        self.written: list[str] = []

    async def exec(self, command, args=None, **kw):
        self.exec_calls.append((command, args))
        if command == "mkdir" and not self.mkdir_ok:
            return _ExecResult(success=False, stderr_text="no mkdir")
        return _ExecResult(success=True)

    async def write_file(self, path, content):
        self.written.append(path)


def _registry(tmp_path, cfg):
    return SandboxRegistry(cfg, store=StateStore(path=tmp_path / "s.json"))


def test_ensure_workdir_uses_mkdir(tmp_path):
    reg = _registry(tmp_path, _Cfg())
    sb = _FakeSandbox(mkdir_ok=True)
    asyncio.run(reg._ensure_workdir("a", sb))
    assert ("mkdir", ["-p", "/work"]) in sb.exec_calls
    assert sb.written == []  # no marker fallback needed


def test_ensure_workdir_is_idempotent(tmp_path):
    reg = _registry(tmp_path, _Cfg())
    sb = _FakeSandbox(mkdir_ok=True)
    asyncio.run(reg._ensure_workdir("a", sb))
    asyncio.run(reg._ensure_workdir("a", sb))
    assert sb.exec_calls.count(("mkdir", ["-p", "/work"])) == 1


def test_ensure_workdir_falls_back_to_fs_when_no_mkdir(tmp_path):
    reg = _registry(tmp_path, _Cfg())
    sb = _FakeSandbox(mkdir_ok=False)
    asyncio.run(reg._ensure_workdir("b", sb))
    assert sb.written == ["/work/.keep"]  # image-agnostic fallback


def test_ensure_workdir_noop_when_workdir_empty(tmp_path):
    class _Empty(_Cfg):
        workdir = ""

    reg = _registry(tmp_path, _Empty())
    sb = _FakeSandbox()
    asyncio.run(reg._ensure_workdir("c", sb))
    assert sb.exec_calls == []
    assert sb.written == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
