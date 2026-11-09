import asyncio
from types import SimpleNamespace

import pytest
from agent_sandbox.control_plane import ControlPlane
from agent_sandbox.errors import ControlPlaneError


@pytest.mark.parametrize(
    ("status", "details", "suffix"),
    [
        ("FAILED", {"error": "Execution role could not be assumed"},
         ": Execution role could not be assumed"),
        ("TERMINATED", {"stateReason": "Runtime hook failed"}, ": Runtime hook failed"),
        ("TERMINATED", {}, ""),
    ],
)
def test_startup_failure_includes_provider_reason(status, details, suffix):
    client = SimpleNamespace(
        get_microvm=lambda **_: {"microvmId": "vm-1", "status": status, **details}
    )
    control = ControlPlane(client=client)

    with pytest.raises(ControlPlaneError) as failure:
        asyncio.run(control.wait_until_running("vm-1"))

    assert str(failure.value) == f"MicroVM vm-1 entered terminal status {status}{suffix}"
