"""Tests for CLI egress connector resolution precedence."""

from __future__ import annotations

import pytest
import typer
from agent_sandbox.cli import main


def _patch_infra(monkeypatch, outputs):
    monkeypatch.setattr(main, "_infra_outputs", lambda: outputs)


def test_explicit_connector_wins(monkeypatch):
    _patch_infra(monkeypatch, {"egress_network_connector_arn": "arn:infra"})
    assert main._resolve_egress_connectors("arn:explicit", False) == ["arn:explicit"]
    # explicit still wins even if --egress is also set
    assert main._resolve_egress_connectors("arn:explicit", True) == ["arn:explicit"]


def test_egress_flag_pulls_from_infra(monkeypatch):
    _patch_infra(monkeypatch, {"egress_network_connector_arn": "arn:infra"})
    assert main._resolve_egress_connectors(None, True) == ["arn:infra"]


def test_none_by_default(monkeypatch):
    monkeypatch.setattr(main, "_infra_outputs", lambda: {})
    assert main._resolve_egress_connectors(None, False) is None


def test_egress_flag_without_infra_output_fails(monkeypatch):
    monkeypatch.setattr(main, "_infra_outputs", lambda: {})
    with pytest.raises(typer.Exit):
        main._resolve_egress_connectors(None, True)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
