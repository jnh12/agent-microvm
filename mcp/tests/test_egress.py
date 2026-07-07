"""Unit tests for MCP egress connector resolution (no AWS needed)."""

from __future__ import annotations

import pytest
from agent_sandbox.errors import SandboxError

from agent_sandbox_mcp.session import resolve_egress_connectors


class _Config:
    """Minimal stand-in exposing just egress_connector_arn()."""

    def __init__(self, arn=None):
        self._arn = arn

    def egress_connector_arn(self):
        return self._arn


def test_explicit_connector_wins():
    cfg = _Config(arn="arn:infra")
    assert resolve_egress_connectors(cfg, "arn:explicit", False) == ["arn:explicit"]
    assert resolve_egress_connectors(cfg, "arn:explicit", True) == ["arn:explicit"]


def test_egress_flag_pulls_configured_arn():
    cfg = _Config(arn="arn:infra")
    assert resolve_egress_connectors(cfg, None, True) == ["arn:infra"]


def test_none_by_default():
    assert resolve_egress_connectors(_Config(arn="arn:infra"), None, False) is None


def test_egress_flag_without_configured_arn_raises():
    with pytest.raises(SandboxError):
        resolve_egress_connectors(_Config(arn=None), None, True)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
