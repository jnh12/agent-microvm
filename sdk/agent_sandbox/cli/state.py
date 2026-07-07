"""Local state store mapping sandbox names to MicroVM identity.

AWS Lambda MicroVMs has no notion of a human-friendly sandbox name, so the CLI
keeps a small JSON store (default ``~/.agent_sandbox/state.json``) mapping
``name -> {microvm_id, endpoint, image_arn, region}``. This is what lets
``asb create --name app`` and a later ``asb exec app`` (a separate process)
resolve to the same MicroVM.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def _default_path() -> Path:
    override = os.environ.get("AGENT_SANDBOX_STATE")
    if override:
        return Path(override)
    return Path.home() / ".agent_sandbox" / "state.json"


@dataclass(slots=True)
class SandboxRecord:
    name: str
    microvm_id: str
    endpoint: str | None = None
    image_arn: str | None = None
    region: str | None = None


class StateStore:
    """A tiny JSON-backed name -> record store."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._records: dict[str, SandboxRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        for name, rec in data.get("sandboxes", {}).items():
            self._records[name] = SandboxRecord(**rec)

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sandboxes": {name: asdict(rec) for name, rec in self._records.items()}}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._path)

    def put(self, record: SandboxRecord) -> None:
        self._records[record.name] = record
        self._flush()

    def get(self, name: str) -> SandboxRecord | None:
        return self._records.get(name)

    def require(self, name: str) -> SandboxRecord:
        rec = self.get(name)
        if rec is None:
            raise KeyError(f"No sandbox named '{name}' in local state ({self._path}).")
        return rec

    def delete(self, name: str) -> None:
        if name in self._records:
            del self._records[name]
            self._flush()

    def all(self) -> list[SandboxRecord]:
        return list(self._records.values())
