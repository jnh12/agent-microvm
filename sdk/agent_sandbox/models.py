"""Data models: sandbox lifecycle states, configuration, and exec results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agent_sandbox.ports import AGENT_PORT


class SandboxState(StrEnum):
    """Lifecycle states, mirroring microsandbox's sandbox lifecycle.

    Values are mapped from the AWS ``lambda-microvms`` MicroVM status where
    applicable (see :func:`from_aws_status`).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @classmethod
    def from_aws_status(cls, status: str | None) -> SandboxState:
        mapping = {
            "PENDING": cls.PENDING,
            "STARTING": cls.PENDING,
            "RUNNING": cls.RUNNING,
            "SUSPENDING": cls.RUNNING,
            "SUSPENDED": cls.SUSPENDED,
            "RESUMING": cls.PENDING,
            "TERMINATING": cls.STOPPED,
            "TERMINATED": cls.STOPPED,
            "FAILED": cls.FAILED,
        }
        return mapping.get((status or "").upper(), cls.UNKNOWN)


@dataclass(slots=True)
class IdlePolicy:
    """Auto suspend/resume policy passed to ``run_microvm``.

    Defaults auto-suspend after 15 minutes idle and auto-resume on next request.
    """

    max_idle_duration_seconds: int = 900
    suspended_duration_seconds: int = 300
    auto_resume_enabled: bool = True

    def to_aws(self) -> dict:
        return {
            "maxIdleDurationSeconds": self.max_idle_duration_seconds,
            "suspendedDurationSeconds": self.suspended_duration_seconds,
            "autoResumeEnabled": self.auto_resume_enabled,
        }


@dataclass(slots=True)
class SandboxConfig:
    """Configuration for creating a sandbox."""

    name: str
    image_arn: str
    cpus: int = 1
    memory: int = 512
    execution_role_arn: str | None = None
    region: str | None = None
    idle_policy: IdlePolicy = field(default_factory=IdlePolicy)
    agent_port: int = AGENT_PORT
    # Optional network connectors (ARNs). Empty -> image/default egress.
    ingress_network_connectors: list[str] = field(default_factory=list)
    egress_network_connectors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name or len(self.name.encode("utf-8")) > 128:
            raise ValueError("name must be non-empty and <= 128 UTF-8 bytes")


@dataclass(slots=True)
class ExecResult:
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")

    @property
    def success(self) -> bool:
        return self.exit_code == 0
