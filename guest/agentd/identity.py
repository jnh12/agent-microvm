"""Per-VM uniqueness state, driven by the ``/run`` and ``/resume`` hooks.

Every MicroVM resumes from the SAME memory+disk snapshot, so anything derived
from randomness at image-build time is IDENTICAL across VMs (the "snapshot
uniqueness" pitfall). :func:`regenerate` mints fresh, CSPRNG-backed identity and
reseeds the stdlib non-CSPRNG so each running VM diverges. It is called on
``/run`` (first boot from snapshot) and ``/resume``, and is written to a tmpfs
file so processes launched via ``/v1/exec`` can read it.

Both agentd and the hooks server share this module (same process), so reseeding
here is visible to agentd's request handlers too.
"""

from __future__ import annotations

import os
import random
import secrets
import threading
import time

# tmpfs path (per-VM, not part of the shared on-disk snapshot layout).
IDENTITY_PATH = os.environ.get("AGENTD_IDENTITY_PATH", "/run/agentd/identity")

_lock = threading.Lock()
_state: dict[str, object] = {
    "vm_id": None,
    "generation": 0,
    "last_event": None,
    "booted_at": None,
    "run_payload": None,
}


def regenerate(event: str, *, run_payload: bytes | None = None) -> dict[str, object]:
    """Mint a fresh per-VM identity and reseed randomness.

    Uses :mod:`secrets` (a CSPRNG pulling fresh kernel entropy) so the new id is
    unique per VM even though the snapshot is shared, then reseeds :mod:`random`
    so later non-CSPRNG use also diverges. Persisting is best-effort.
    """
    with _lock:
        _state["vm_id"] = secrets.token_hex(16)
        _state["generation"] = int(_state["generation"]) + 1
        _state["last_event"] = event
        _state["booted_at"] = time.time()
        if run_payload is not None:
            _state["run_payload"] = run_payload.decode("utf-8", errors="replace")
        random.seed(secrets.randbits(256))
        _persist(str(_state["vm_id"]))
        return dict(_state)


def current() -> dict[str, object]:
    with _lock:
        return dict(_state)


def _persist(vm_id: str) -> None:
    try:
        os.makedirs(os.path.dirname(IDENTITY_PATH), exist_ok=True)
        with open(IDENTITY_PATH, "w", encoding="utf-8") as fh:
            fh.write(vm_id)
    except OSError:
        # Best-effort: identity is still available in-process via current().
        pass
