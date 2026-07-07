"""Local JSON state for the boto3 infra provisioner.

Replaces Pulumi's state backend with a small self-managed file, mirroring the
sandbox-name store in :mod:`agent_sandbox.cli.state`. It records, per
``project/stack``:

- ``resources``: for each logical resource (role, bucket, guest_object, image,
  security_group) an id and a ``managed`` flag (True if we created it, False if
  the user asked us to reuse an existing one). ``destroy`` only tears down
  ``managed`` resources.
- ``outputs``: the values the SDK/CLI consume (image_arn, execution_role_arn,
  build_bucket, and optional network outputs).

Default path ``~/.agent_sandbox/infra-state.json`` (override with
``AGENT_SANDBOX_INFRA_STATE``).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    override = os.environ.get("AGENT_SANDBOX_INFRA_STATE")
    if override:
        return Path(override)
    return Path.home() / ".agent_sandbox" / "infra-state.json"


@dataclass(slots=True)
class Resource:
    """A tracked infrastructure resource."""

    kind: str
    id: str
    managed: bool = True
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StackState:
    """All tracked resources + outputs for one ``project/stack``."""

    project: str
    stack: str
    resources: dict[str, Resource] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    def set_resource(
        self,
        name: str,
        kind: str,
        id: str,
        *,
        managed: bool = True,
        attrs: dict[str, Any] | None = None,
    ) -> Resource:
        res = Resource(kind=kind, id=id, managed=managed, attrs=attrs or {})
        self.resources[name] = res
        return res

    def get_resource(self, name: str) -> Resource | None:
        return self.resources.get(name)

    def pop_resource(self, name: str) -> Resource | None:
        return self.resources.pop(name, None)


class InfraStateStore:
    """JSON-backed store of :class:`StackState` keyed by ``project/stack``."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._stacks: dict[str, dict[str, Any]] = {}
        # Serializes reads/writes so parallel per-project provisioning (a shared
        # store across threads) can't clobber the single JSON file.
        self._lock = threading.Lock()
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def _key(project: str, stack: str) -> str:
        return f"{project}/{stack}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        self._stacks = data.get("stacks", {})

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"stacks": self._stacks}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._path)

    def load(self, project: str, stack: str) -> StackState:
        with self._lock:
            raw = self._stacks.get(self._key(project, stack))
        if not raw:
            return StackState(project=project, stack=stack)
        resources = {
            name: Resource(
                kind=r.get("kind", ""),
                id=r.get("id", ""),
                managed=bool(r.get("managed", True)),
                attrs=r.get("attrs", {}) or {},
            )
            for name, r in (raw.get("resources") or {}).items()
        }
        return StackState(
            project=project,
            stack=stack,
            resources=resources,
            outputs=raw.get("outputs", {}) or {},
        )

    def save(self, state: StackState) -> None:
        with self._lock:
            self._stacks[self._key(state.project, state.stack)] = {
                "resources": {
                    name: {
                        "kind": r.kind,
                        "id": r.id,
                        "managed": r.managed,
                        "attrs": r.attrs,
                    }
                    for name, r in state.resources.items()
                },
                "outputs": state.outputs,
            }
            self._flush()

    def clear(self, project: str, stack: str) -> None:
        with self._lock:
            self._stacks.pop(self._key(project, stack), None)
            self._flush()
