"""Runtime configuration and host-path policy for the MCP server.

Configuration mirrors microsandbox-mcp's ``MICROSANDBOX_MCP_*`` namespace, using
``AGENT_SANDBOX_MCP_*`` here, and reuses the SDK's existing
``AGENT_SANDBOX_IMAGE_ARN`` / ``AGENT_SANDBOX_EXECUTION_ROLE_ARN`` / region env
vars plus ``asb infra`` output auto-wiring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from agent_sandbox.sandbox import ENV_IMAGE_ARN, ENV_ROLE_ARN

# -- env var names ---------------------------------------------------------

ENV_HOST_PATHS = "AGENT_SANDBOX_MCP_HOST_PATHS"
ENV_HOST_PATH_POLICY = "AGENT_SANDBOX_MCP_HOST_PATH_POLICY"
ENV_ENABLE_DANGEROUS = "AGENT_SANDBOX_MCP_ENABLE_DANGEROUS"
ENV_MAX_OUTPUT_BYTES = "AGENT_SANDBOX_MCP_MAX_OUTPUT_BYTES"
ENV_DEFAULT_TIMEOUT_MS = "AGENT_SANDBOX_MCP_DEFAULT_TIMEOUT_MS"
ENV_REGION = "AGENT_SANDBOX_REGION"
ENV_WORKDIR = "AGENT_SANDBOX_WORKDIR"
ENV_VERIFY_TLS = "AGENT_SANDBOX_VERIFY_TLS"
ENV_EGRESS_CONNECTOR = "AGENT_SANDBOX_EGRESS_CONNECTOR"

# -- defaults --------------------------------------------------------------

DEFAULT_MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB
DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_WORKDIR = "/work"

POLICY_ALLOWLIST = "allowlist"
POLICY_UNRESTRICTED = "unrestricted"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class Config:
    """Effective server configuration, read once from the environment."""

    region: str | None
    workdir: str
    verify_tls: bool
    max_output_bytes: int
    default_timeout_ms: int
    host_path_policy: str
    host_paths: list[str] = field(default_factory=list)
    enable_dangerous: bool = False

    @property
    def default_timeout_seconds(self) -> float:
        return self.default_timeout_ms / 1000.0

    def image_arn(self) -> str | None:
        return os.environ.get(ENV_IMAGE_ARN) or _infra_outputs().get("image_arn")

    def role_arn(self) -> str | None:
        return os.environ.get(ENV_ROLE_ARN) or _infra_outputs().get("execution_role_arn")

    def egress_connector_arn(self) -> str | None:
        return os.environ.get(ENV_EGRESS_CONNECTOR) or _infra_outputs().get(
            "egress_network_connector_arn"
        )

    def build_bucket(self) -> str | None:
        return _infra_outputs().get("build_bucket")

    def infra_outputs(self) -> dict:
        """Copy of the best-effort ``asb infra`` outputs (never raises)."""
        return dict(_infra_outputs())

    def host_path_allowed(self, path: str) -> bool:
        """Whether a host path is permitted for host<->sandbox copy operations."""
        if self.host_path_policy == POLICY_UNRESTRICTED:
            return True
        real = os.path.realpath(path)
        for root in self.host_paths:
            root_real = os.path.realpath(root)
            if real == root_real or real.startswith(root_real + os.sep):
                return True
        return False

    def as_dict(self) -> dict:
        return {
            "region": self.region,
            "workdir": self.workdir,
            "verify_tls": self.verify_tls,
            "max_output_bytes": self.max_output_bytes,
            "default_timeout_ms": self.default_timeout_ms,
            "host_path_policy": self.host_path_policy,
            "host_paths": self.host_paths,
            "enable_dangerous": self.enable_dangerous,
            "image_arn": self.image_arn(),
            "role_arn": self.role_arn(),
        }


@lru_cache(maxsize=1)
def load_config() -> Config:
    raw_paths = os.environ.get(ENV_HOST_PATHS)
    if raw_paths:
        host_paths = [p for p in raw_paths.split(os.pathsep) if p]
    else:
        host_paths = [os.getcwd()]
    policy = (os.environ.get(ENV_HOST_PATH_POLICY) or POLICY_ALLOWLIST).strip().lower()
    if policy not in {POLICY_ALLOWLIST, POLICY_UNRESTRICTED}:
        policy = POLICY_ALLOWLIST
    return Config(
        region=os.environ.get(ENV_REGION) or None,
        workdir=os.environ.get(ENV_WORKDIR) or DEFAULT_WORKDIR,
        verify_tls=_env_bool(ENV_VERIFY_TLS, True),
        max_output_bytes=_env_int(ENV_MAX_OUTPUT_BYTES, DEFAULT_MAX_OUTPUT_BYTES),
        default_timeout_ms=_env_int(ENV_DEFAULT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS),
        host_path_policy=policy,
        host_paths=host_paths,
        enable_dangerous=_env_bool(ENV_ENABLE_DANGEROUS, False),
    )


@lru_cache(maxsize=1)
def _infra_outputs() -> dict:
    """Best-effort ``asb infra`` outputs for auto-wiring image/role ARNs.

    Never raises; returns ``{}`` when infra state isn't available so callers
    fall back to env vars.
    """
    try:
        from agent_sandbox.infra.config import load_setup
        from agent_sandbox.infra.provisioner import read_outputs

        return read_outputs(load_setup())
    except Exception:  # noqa: BLE001 - auto-wire is optional
        return {}
