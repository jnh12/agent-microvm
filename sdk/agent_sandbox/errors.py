"""Exception hierarchy for the agent_sandbox SDK."""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for all SDK errors."""


class ControlPlaneError(SandboxError):
    """Raised when an AWS ``lambda-microvms`` control-plane call fails."""


class AgentError(SandboxError):
    """Raised when the in-VM guest agent (``agentd``) returns an error."""


class SandboxTimeoutError(SandboxError):
    """Raised when a sandbox does not reach the expected state in time."""
