"""Shared in-process state, visible to both server threads.

agentd (app plane) and the lifecycle hooks (:mod:`agentd.hooks`) run in the same
process but on separate threads/event loops (see ``serve.py``), so this module
uses plain :mod:`threading` primitives rather than asyncio ones:

* :data:`ready` — set once agentd's app has started; the ``/ready`` hook reads it
  so the platform snapshots only a fully-booted agentd.
* :data:`inflight` — count of currently-running ``exec`` calls, so the
  ``/suspend`` hook can drain in-flight work before the VM is frozen.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

# Set by agentd's startup event; read by the /ready hook.
ready = threading.Event()

_lock = threading.Lock()
_inflight = 0
_drained = threading.Condition(_lock)


@contextmanager
def track() -> Iterator[None]:
    """Count one in-flight exec for the duration of the ``with`` block."""
    global _inflight
    with _lock:
        _inflight += 1
    try:
        yield
    finally:
        with _lock:
            _inflight -= 1
            if _inflight == 0:
                _drained.notify_all()


def inflight() -> int:
    with _lock:
        return _inflight


def wait_drain(timeout: float) -> bool:
    """Block until no exec is in flight, or ``timeout`` elapses.

    Returns ``True`` if fully drained, ``False`` on timeout. Used by ``/suspend``
    so the snapshot isn't taken mid-command.
    """
    with _drained:
        if _inflight == 0:
            return True
        # wait_for returns True when the predicate held (drained), False on timeout.
        drained = _drained.wait_for(lambda: _inflight == 0, timeout=timeout)
    return drained
