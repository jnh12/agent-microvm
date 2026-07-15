"""Locate the guest image source (Dockerfile + ``agentd``).

A ``pip install agent-sandbox-os`` ships a bundled copy of ``guest/`` (see the
``force-include`` in ``pyproject.toml``) so image builds work without a repo
checkout, while still letting users override it with their own guest directory.

Resolution precedence:

1. An explicit directory the caller passed (CLI arg / a custom
   ``image.guest_dir``). If given but missing/invalid, that's an error — we
   don't silently mask a typo with the bundled copy.
2. ``./guest`` (a repo checkout or a user override placed next to the config).
3. The copy bundled inside this installed package.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

_BUNDLED_PKG = "agent_sandbox"
_BUNDLED_DIR = "_bundled_guest"


def is_guest_dir(path: str) -> bool:
    """A directory qualifies as guest source only if it has a Dockerfile."""
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "Dockerfile"))


def bundled_guest_dir() -> str | None:
    """Filesystem path to the guest source that ships with this install.

    Two cases, in order:

    1. A wheel install: the ``guest/`` copy force-included into the package as
       ``agent_sandbox/_bundled_guest``.
    2. A source/editable checkout (where force-include never ran): the repo's
       top-level ``guest/`` sitting next to the ``sdk/`` tree, so ``asb`` works
       from any directory during development too.

    Returns ``None`` when neither exists (or a zipimport loader makes the
    packaged copy non-materialized), so the caller falls back to an explicit /
    ``./guest`` path.
    """
    # 1. Wheel: force-included copy inside the installed package.
    try:
        packaged = os.fspath(resources.files(_BUNDLED_PKG).joinpath(_BUNDLED_DIR))
        if is_guest_dir(packaged):
            return packaged
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        pass

    # 2. Editable/source checkout: <repo>/guest, next to <repo>/sdk/agent_sandbox.
    try:
        import agent_sandbox

        repo_guest = Path(agent_sandbox.__file__).resolve().parents[2] / "guest"
        if is_guest_dir(str(repo_guest)):
            return str(repo_guest)
    except (AttributeError, IndexError, TypeError):
        pass

    return None


def resolve_guest_dir(candidate: str, *, allow_bundled_fallback: bool) -> str:
    """Return an existing guest dir for ``candidate``, else the bundled copy.

    ``allow_bundled_fallback`` gates whether a missing ``candidate`` falls back
    to the bundled guest (``True`` for the default ``./guest``) or is surfaced
    as an error (``False`` for an explicit custom path the user typed).
    """
    if is_guest_dir(candidate):
        return candidate
    if allow_bundled_fallback:
        bundled = bundled_guest_dir()
        if bundled is not None:
            return bundled
    raise FileNotFoundError(
        f"no guest source at {candidate!r} (needs a Dockerfile) and no bundled "
        "guest is available; pass a valid --directory or set image.guest_dir"
    )
