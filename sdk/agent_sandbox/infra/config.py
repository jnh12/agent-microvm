"""Typed configuration loaded from ``sandbox.yaml``.

``sandbox.yaml`` is the single user-facing source of truth for infrastructure.
Each resource follows a reuse-or-create rule: if the user supplies an existing
identifier, it is reused; otherwise it is created.

The default filename is ``sandbox.yaml``; the legacy ``setup.yaml`` name is still
picked up automatically when present. Override the path with the
``AGENT_SANDBOX_SETUP`` environment variable or an explicit ``-f`` on the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SETUP_FILE = "sandbox.yaml"
LEGACY_SETUP_FILE = "setup.yaml"
ENV_SETUP = "AGENT_SANDBOX_SETUP"


class SetupError(ValueError):
    """Raised when setup.yaml is missing or invalid."""


@dataclass(slots=True)
class ImageConfig:
    name: str = "agent-sandbox-guest"
    guest_dir: str = "./guest"
    # Managed base image the MicroVM image is built on top of. Empty -> the
    # newest managed base image is auto-discovered (e.g. al2023).
    base_image_arn: str = ""
    base_image_version: str = ""


@dataclass(slots=True)
class RoleConfig:
    arn: str = ""
    name: str = "agent-sandbox-exec"
    extra_policy_arns: list[str] = field(default_factory=list)

    @property
    def reuse(self) -> bool:
        return bool(self.arn)


@dataclass(slots=True)
class BucketConfig:
    name: str = ""

    @property
    def reuse(self) -> bool:
        return bool(self.name)


@dataclass(slots=True)
class EgressConfig:
    """VPC egress network connector (reuse-or-create).

    Set ``connector_arn`` to reuse an existing ``VPC_EGRESS`` Lambda Network
    Connector. Otherwise, populating any create-from-scratch field opts into
    building one (operator role + egress SG + connector).
    """

    connector_arn: str = ""
    name: str = "agent-sandbox-egress"
    vpc_id: str = ""
    subnet_ids: list[str] = field(default_factory=list)
    security_group_id: str = ""
    operator_role_arn: str = ""

    @property
    def reuse(self) -> bool:
        return bool(self.connector_arn)

    @property
    def reuse_sg(self) -> bool:
        return bool(self.security_group_id)

    @property
    def enabled(self) -> bool:
        """True if the user opted into an egress connector at all."""
        return bool(
            self.connector_arn
            or self.vpc_id
            or self.subnet_ids
            or self.security_group_id
            or self.operator_role_arn
        )


@dataclass(slots=True)
class NetworkConfig:
    egress: EgressConfig = field(default_factory=EgressConfig)

    @property
    def enabled(self) -> bool:
        """True if the user opted into any networking (egress connector)."""
        return self.egress.enabled


@dataclass(slots=True)
class InfraConfig:
    project: str = "agent-sandbox-os"
    stack: str = "dev"
    region: str = "us-east-1"
    image: ImageConfig = field(default_factory=ImageConfig)
    role: RoleConfig = field(default_factory=RoleConfig)
    bucket: BucketConfig = field(default_factory=BucketConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    # Absolute path to the directory containing setup.yaml, for resolving
    # relative paths like image.guest_dir.
    base_dir: str = "."

    @property
    def guest_dir_abs(self) -> str:
        path = Path(self.image.guest_dir)
        if not path.is_absolute():
            path = Path(self.base_dir) / path
        return str(path.resolve())

    def validate(self) -> None:
        if not self.project:
            raise SetupError("`project` must be non-empty")
        if not self.stack:
            raise SetupError("`stack` must be non-empty")
        if not self.region:
            raise SetupError("`region` must be non-empty")
        if not os.path.isdir(self.guest_dir_abs):
            raise SetupError(
                f"image.guest_dir does not exist: {self.image.guest_dir} "
                f"(resolved to {self.guest_dir_abs})"
            )
        if not os.path.isfile(os.path.join(self.guest_dir_abs, "Dockerfile")):
            raise SetupError(f"no Dockerfile found in guest_dir: {self.guest_dir_abs}")

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_dir: str = ".") -> InfraConfig:
        data = data or {}
        image = data.get("image") or {}
        role = data.get("role") or {}
        bucket = data.get("bucket") or {}
        network = data.get("network") or {}
        egress = network.get("egress") or {}
        # Read defaults off default instances (class-attribute access on a
        # slots=True dataclass yields slot descriptors, not the default values).
        d = cls()
        d_img, d_role = ImageConfig(), RoleConfig()
        return cls(
            project=data.get("project", d.project),
            stack=data.get("stack", d.stack),
            region=data.get("region", d.region),
            image=ImageConfig(
                name=image.get("name", d_img.name),
                guest_dir=image.get("guest_dir", d_img.guest_dir),
                base_image_arn=image.get("base_image_arn", ""),
                base_image_version=image.get("base_image_version", ""),
            ),
            role=RoleConfig(
                arn=role.get("arn", ""),
                name=role.get("name", d_role.name),
                extra_policy_arns=list(role.get("extra_policy_arns", []) or []),
            ),
            bucket=BucketConfig(name=bucket.get("name", "")),
            network=NetworkConfig(
                egress=EgressConfig(
                    connector_arn=egress.get("connector_arn", ""),
                    name=egress.get("name", EgressConfig().name),
                    vpc_id=egress.get("vpc_id", ""),
                    subnet_ids=list(egress.get("subnet_ids", []) or []),
                    security_group_id=egress.get("security_group_id", ""),
                    operator_role_arn=egress.get("operator_role_arn", ""),
                ),
            ),
            base_dir=base_dir,
        )


def _require_yaml():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - guarded by [infra] extra
        raise SetupError(
            "PyYAML is required to read sandbox.yaml. Install with "
            "`pip install agent-sandbox-os[infra]`."
        ) from exc
    return yaml


def _default_setup_path() -> Path:
    """Resolve the default config path.

    Order: ``$AGENT_SANDBOX_SETUP`` -> ``sandbox.yaml`` -> legacy ``setup.yaml``.
    Returns the primary name when none exist so error messages point at it.
    """
    override = os.environ.get(ENV_SETUP)
    if override:
        return Path(override)
    primary = Path(DEFAULT_SETUP_FILE)
    if primary.is_file():
        return primary
    legacy = Path(LEGACY_SETUP_FILE)
    if legacy.is_file():
        return legacy
    return primary


def load_setup(path: str | os.PathLike[str] | None = None) -> InfraConfig:
    """Load and validate a single config file.

    Search order when ``path`` is None: ``$AGENT_SANDBOX_SETUP`` then
    ``./sandbox.yaml`` then legacy ``./setup.yaml`` in the current directory.
    """
    yaml = _require_yaml()

    path = Path(path) if path is not None else _default_setup_path()
    if not path.is_file():
        raise SetupError(
            f"config file not found: {path}. Run `asb infra init` to scaffold a "
            "sandbox.yaml."
        )

    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SetupError(f"{path} must contain a YAML mapping at the top level")

    config = InfraConfig.from_dict(data, base_dir=str(path.resolve().parent))
    config.validate()
    return config


def _resolve_setup_paths(paths: list[str] | None) -> list[Path]:
    """Expand CLI ``-f`` inputs (files/dirs) into a list of config file paths."""
    if not paths:
        default = _default_setup_path()
        if not default.is_file():
            raise SetupError(
                f"config file not found: {default}. Run `asb infra init` to "
                "scaffold a sandbox.yaml."
            )
        return [default]
    resolved: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files = sorted(
                f for f in path.iterdir()
                if f.is_file() and f.suffix in (".yaml", ".yml")
            )
            if not files:
                raise SetupError(f"no .yaml/.yml files in directory: {path}")
            resolved.extend(files)
        elif path.is_file():
            resolved.append(path)
        else:
            raise SetupError(f"config file not found: {path}")
    return resolved


def load_setups(
    paths: list[str] | None = None, *, stack: str | None = None
) -> list[InfraConfig]:
    """Load one or more config files (kubectl-style).

    Each path may be a file or a directory (expanded to sorted ``*.yaml``/``*.yml``,
    non-recursive). A file may contain multiple ``---``-separated documents; each
    mapping document becomes one :class:`InfraConfig`. When ``paths`` is empty the
    default config file is used. ``stack`` overrides every loaded config's stack.
    """
    yaml = _require_yaml()
    configs: list[InfraConfig] = []
    for file_path in _resolve_setup_paths(paths):
        with open(file_path) as fh:
            try:
                docs = list(yaml.safe_load_all(fh))
            except yaml.YAMLError as exc:
                raise SetupError(f"{file_path}: invalid YAML: {exc}") from exc
        base_dir = str(file_path.resolve().parent)
        found = False
        for i, doc in enumerate(docs):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise SetupError(
                    f"{file_path} (document {i + 1}): must be a YAML mapping"
                )
            config = InfraConfig.from_dict(doc, base_dir=base_dir)
            if stack:
                config.stack = stack
            config.validate()
            configs.append(config)
            found = True
        if not found:
            raise SetupError(f"{file_path}: no config documents found")
    return configs
