"""Drive the Pulumi program via the Automation API.

Wraps ``pulumi.automation`` so ``asb infra`` can preview/up/destroy/refresh and
read outputs in-process, using an inline program (no separate program dir). The
``pulumi`` CLI must be installed; a clear error is raised otherwise.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from typing import Any

from agent_sandbox.infra.config import InfraConfig


class InfraError(RuntimeError):
    """Raised for Automation API / pulumi CLI problems."""


def _ensure_pulumi_cli() -> None:
    if shutil.which("pulumi") is None:
        raise InfraError(
            "The `pulumi` CLI was not found on PATH. Install it "
            "(https://www.pulumi.com/docs/install/) and ensure `pulumi version` works."
        )


def _auto():
    try:
        from pulumi import automation as auto
    except ImportError as exc:
        raise InfraError(
            "Pulumi is not installed. Install the infra extra: "
            "`pip install agent-sandbox-os[infra]`."
        ) from exc
    return auto


class InfraRunner:
    """A thin facade over a single Automation API stack."""

    def __init__(self, cfg: InfraConfig) -> None:
        _ensure_pulumi_cli()
        self._cfg = cfg
        self._auto = _auto()
        self._stack = self._ensure_stack()

    def _program(self) -> Callable[[], None]:
        from agent_sandbox.infra.program import build

        cfg = self._cfg
        return lambda: build(cfg)

    def _workspace_opts(self):
        auto = self._auto
        backend_url = self._cfg.backend.resolved_url()
        project_settings = auto.ProjectSettings(
            name=self._cfg.project,
            runtime="python",
            backend=auto.ProjectBackend(url=backend_url),
        )
        # Use a passphrase secrets provider so runs are non-interactive. For a
        # self-managed backend this needs PULUMI_CONFIG_PASSPHRASE; default to
        # empty so `asb infra` works out of the box without a Pulumi account.
        passphrase = os.environ.get("PULUMI_CONFIG_PASSPHRASE", "")
        return auto.LocalWorkspaceOptions(
            project_settings=project_settings,
            secrets_provider="passphrase",
            env_vars={"PULUMI_CONFIG_PASSPHRASE": passphrase},
        )

    def _ensure_stack(self):
        stack = self._auto.create_or_select_stack(
            stack_name=self._cfg.stack,
            project_name=self._cfg.project,
            program=self._program(),
            opts=self._workspace_opts(),
        )
        # Pulumi auto-installs the AWS provider plugin version matching the
        # installed `pulumi-aws` SDK on first preview/up; don't pin a version
        # here (a wrong pin 403s against the plugin CDN).
        stack.set_config("aws:region", self._auto.ConfigValue(value=self._cfg.region))
        return stack

    def preview(self) -> None:
        self._stack.preview(on_output=print)

    def up(self) -> dict[str, Any]:
        result = self._stack.up(on_output=print)
        return {k: v.value for k, v in result.outputs.items()}

    def refresh(self) -> None:
        self._stack.refresh(on_output=print)

    def destroy(self) -> None:
        self._stack.destroy(on_output=print)

    def outputs(self) -> dict[str, Any]:
        return {k: v.value for k, v in self._stack.outputs().items()}


def try_read_outputs(cfg: InfraConfig) -> dict[str, Any]:
    """Best-effort read of a stack's outputs for CLI auto-wiring.

    Returns an empty dict if pulumi/the stack is unavailable, so callers can
    silently fall back to env vars / explicit flags.
    """
    try:
        return InfraRunner(cfg).outputs()
    except Exception:  # noqa: BLE001 - auto-wire must never hard-fail
        return {}
