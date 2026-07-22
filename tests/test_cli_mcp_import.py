"""Tests for `asb mcp` import diagnostics (no AWS, no mcp extra needed).

A missing `mcp` extra and an *installed but unloadable* one are different
failures: the first needs an install hint, the second needs the underlying
error. Reporting the second as the first sends people off installing a package
they already have.
"""

from __future__ import annotations

import builtins

from agent_sandbox.cli.main import _is_missing, app
from typer.testing import CliRunner

runner = CliRunner()

# A compiled dependency built for the wrong architecture raises a bare
# ImportError from dlopen -- not ModuleNotFoundError.
ARCH_ERROR = ImportError(
    "dlopen(/x/pydantic_core/_pydantic_core.cpython-312-darwin.so, 0x0002): "
    "tried: '/x/...' (mach-o file, but is an incompatible architecture "
    "(have 'x86_64', need 'arm64e' or 'arm64'))"
)


def test_missing_module_is_reported_as_missing():
    exc = ModuleNotFoundError("No module named 'mcp'", name="mcp")
    assert _is_missing(exc, "agent_sandbox_mcp", "mcp", "fastmcp")


def test_missing_submodule_counts_as_the_extra():
    exc = ModuleNotFoundError("No module named 'mcp.server'", name="mcp.server.fastmcp")
    assert _is_missing(exc, "mcp")


def test_arch_mismatch_is_not_reported_as_missing():
    assert not _is_missing(ARCH_ERROR, "agent_sandbox_mcp", "mcp", "fastmcp")


def test_unrelated_missing_module_is_not_the_extra():
    exc = ModuleNotFoundError("No module named 'pydantic_core'", name="pydantic_core")
    assert not _is_missing(exc, "agent_sandbox_mcp", "mcp")


def _patch_import(monkeypatch, exc: ImportError):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("agent_sandbox_mcp"):
            raise exc
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_mcp_command_hints_at_the_extra_when_absent(monkeypatch):
    _patch_import(monkeypatch, ModuleNotFoundError("No module named 'mcp'", name="mcp"))
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 1
    # The published distribution is `asbox`; `agent-sandbox-os` does not exist.
    assert "asbox" in result.output
    assert "agent-sandbox-os" not in result.output


def test_mcp_command_surfaces_the_real_error_when_broken(monkeypatch):
    _patch_import(monkeypatch, ARCH_ERROR)
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 1
    assert "incompatible architecture" in result.output
    assert "failed to load" in result.output
    # Must not send the user off to install something they already have.
    assert "Install the `mcp` extra" not in result.output
