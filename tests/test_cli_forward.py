"""Tests for `asb forward` auto-disconnect status logic."""

from __future__ import annotations

import pytest
from agent_sandbox.cli.main import _vm_is_gone


@pytest.mark.parametrize(
    "status",
    ["RUNNING", "running", "PENDING", "STARTING", "CREATING", "unknown", None, ""],
)
def test_vm_is_gone_keeps_serving_for_live_or_transient(status):
    assert _vm_is_gone(status) is False


@pytest.mark.parametrize(
    "status",
    [
        "SUSPENDED",
        "SUSPENDING",
        "STOPPING",
        "STOPPED",
        "TERMINATING",
        "TERMINATED",
        "FAILED",
        "suspended",
        "  Terminated  ",
    ],
)
def test_vm_is_gone_triggers_disconnect_for_terminal_states(status):
    assert _vm_is_gone(status) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
