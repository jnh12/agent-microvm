"""Process entrypoint: serve agentd and the lifecycle hooks side by side.

One container runs one ``CMD``, so this single process starts both listeners:

* agentd's application API on ``AGENT_PORT`` (main thread) — fronted by the
  platform's auth-token proxy, this is what the SDK/CLI talk to.
* the lifecycle-hook server on ``HOOK_PORT`` (daemon thread) — called only by the
  platform. Bound to ``0.0.0.0`` because the platform reaches it over the guest's
  network interface, not loopback.

Running both in one process lets the hooks reseed state agentd shares (see
``identity``/``state``); running the hooks on their own thread keeps ``/suspend``
responsive even while agentd is busy.
"""

from __future__ import annotations

import logging
import os
import threading

import uvicorn

from agentd.ports import AGENT_PORT, HOOK_PORT

log = logging.getLogger("agentd.serve")


def _serve_hooks() -> None:
    # The hook server is required: the platform's /ready gates image build, and
    # /suspend etc. gate runtime transitions. If it ever exits — failed bind,
    # import error — don't let agentd keep the container alive without it (which
    # would surface only as an opaque /ready timeout). Crash the process instead.
    try:
        uvicorn.run(
            "agentd.hooks:hooks_app",
            host="0.0.0.0",
            port=HOOK_PORT,
            log_level="warning",
        )
    except BaseException:
        log.exception("hook server crashed")
    finally:
        os._exit(1)


def main() -> None:
    threading.Thread(target=_serve_hooks, name="agentd-hooks", daemon=True).start()
    uvicorn.run(
        "agentd.app:app",
        host="0.0.0.0",
        port=AGENT_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
