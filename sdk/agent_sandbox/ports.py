"""Well-known guest ports, as a single source of truth for the SDK/host side.

Two logical planes live in every MicroVM (see the guest agent):

* ``AGENT_PORT`` — agentd's HTTP API (exec/fs). This is the *application* plane,
  reached by clients through the ``:443`` auth-token proxy. Auth tokens are
  scoped to this port by default.
* ``HOOK_PORT`` — the lifecycle-hook server. This is the *lifecycle* plane,
  called only by the AWS Lambda MicroVMs platform over its private route. Clients
  must never be able to forward to it (see the ``forward`` CLI guard).

Both are overridable via env so operators aren't locked to the defaults. The
guest mirrors these values in :mod:`agentd.ports`; the SDK ``HOOK_PORT`` (sent to
the platform via ``--hooks``) MUST match the port the guest actually binds.
"""

from __future__ import annotations

import os

AGENT_PORT: int = int(os.getenv("AGENT_SANDBOX_AGENT_PORT", "8080"))
HOOK_PORT: int = int(os.getenv("AGENT_SANDBOX_HOOK_PORT", "9000"))
