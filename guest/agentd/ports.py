"""Well-known ports for the guest, as a single source of truth.

``AGENT_PORT`` is agentd's application API (exec/fs), fronted by the platform's
auth-token proxy. ``HOOK_PORT`` is the lifecycle-hook server, called only by the
platform. Both are overridable via env; the Dockerfile bakes the defaults.

Invariant: ``HOOK_PORT`` here (what ``serve.py`` binds) must equal the SDK's
``agent_sandbox.ports.HOOK_PORT`` (what ``--hooks`` tells the platform). Both
default to 9000; override consistently on both sides.
"""

from __future__ import annotations

import os

AGENT_PORT: int = int(os.getenv("AGENTD_PORT", "8080"))
HOOK_PORT: int = int(os.getenv("AGENTD_HOOK_PORT", "9000"))
