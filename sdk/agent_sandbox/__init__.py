"""agent_sandbox: embeddable Python SDK for microVM sandboxes on AWS Lambda MicroVMs.

A Python fork of the microsandbox developer experience. The local ``libkrun``
runtime is replaced by AWS Lambda MicroVMs (managed Firecracker); the SDK talks
to the ``lambda-microvms`` control plane and to a guest agent (``agentd``) baked
into the MicroVM image over its dedicated HTTPS endpoint.
"""

from agent_sandbox.errors import (
    AgentError,
    ControlPlaneError,
    SandboxError,
    SandboxTimeoutError,
)
from agent_sandbox.models import ExecResult, SandboxConfig, SandboxState
from agent_sandbox.sandbox import Sandbox

__all__ = [
    "Sandbox",
    "SandboxConfig",
    "SandboxState",
    "ExecResult",
    "SandboxError",
    "ControlPlaneError",
    "AgentError",
    "SandboxTimeoutError",
]

__version__ = "0.2.1"
