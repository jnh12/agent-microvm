"""Shared sandbox registry backing the MCP tools.

Maps human-friendly sandbox names to live :class:`~agent_sandbox.Sandbox` objects
and persists their identity via the SDK's CLI state store, so sandboxes created
through the MCP server are also visible to the ``asb`` CLI (and vice versa). Also
owns per-region :class:`~agent_sandbox.control_plane.ControlPlane` clients and
resolves image / role / egress-connector defaults from :class:`Config`.
"""

from __future__ import annotations

from agent_sandbox import Sandbox
from agent_sandbox.cli.state import SandboxRecord, StateStore
from agent_sandbox.control_plane import ControlPlane
from agent_sandbox.errors import SandboxError

from agent_sandbox_mcp.config import Config


def resolve_egress_connectors(
    config: Config, egress_connector: str | None, use_infra: bool
) -> list[str] | None:
    """Resolve the egress network connector(s) to attach at run time.

    Precedence: an explicit ``egress_connector`` ARN wins; otherwise ``use_infra``
    pulls the connector configured via env / ``asb infra`` outputs. Returns
    ``None`` when no connector should be attached (the default).
    """
    if egress_connector:
        return [egress_connector]
    if use_infra:
        arn = config.egress_connector_arn()
        if not arn:
            raise SandboxError(
                "egress=true but no egress connector is configured. Set "
                "network.egress in setup.yaml and run `asb infra up`, set "
                "$AGENT_SANDBOX_EGRESS_CONNECTOR, or pass egress_connector."
            )
        return [arn]
    return None


class SandboxRegistry:
    """Name -> live Sandbox cache plus shared clients and state persistence."""

    def __init__(self, config: Config, store: StateStore | None = None) -> None:
        self._config = config
        self.store = store or StateStore()
        self._live: dict[str, Sandbox] = {}
        self._control_planes: dict[str | None, ControlPlane] = {}
        # Names whose working directory has been ensured this session.
        self._workdir_ready: set[str] = set()

    # -- clients -----------------------------------------------------------

    def control_plane(self, region: str | None = None) -> ControlPlane:
        key = region or self._config.region
        if key not in self._control_planes:
            self._control_planes[key] = ControlPlane(region=key)
        return self._control_planes[key]

    # -- records -----------------------------------------------------------

    def records(self) -> list[SandboxRecord]:
        return self.store.all()

    # -- working directory -------------------------------------------------

    async def _ensure_workdir(self, name: str, sandbox: Sandbox) -> None:
        """Make sure the configured working directory exists in the sandbox.

        Different images ship different layouts, so the MCP can't assume its
        default workdir (e.g. ``/work``) exists. Exec/shell tools pass this as the
        subprocess ``cwd``, and a missing ``cwd`` makes the guest raise
        ``FileNotFoundError`` (surfaced as a misleading "command not found"). We
        create it once per sandbox, image-agnostically: try ``mkdir -p`` (no
        ``cwd`` so it can't hit the same problem), and fall back to the guest's
        filesystem endpoint (which does a server-side ``os.makedirs`` and needs no
        in-VM binary, so it works even on distroless images).
        """
        workdir = self._config.workdir
        if not workdir or name in self._workdir_ready:
            return
        try:
            result = await sandbox.exec("mkdir", ["-p", workdir])
            if not result.success:
                raise RuntimeError(result.stderr_text.strip() or "mkdir failed")
        except Exception:  # noqa: BLE001 - fall back; never block on bootstrap
            try:
                await sandbox.write_file(f"{workdir.rstrip('/')}/.keep", b"")
            except Exception:  # noqa: BLE001 - best-effort
                return
        self._workdir_ready.add(name)

    # -- creation ----------------------------------------------------------

    def _resolve_image(self, image: str | None) -> str:
        resolved = image or self._config.image_arn()
        if not resolved:
            raise SandboxError(
                "No MicroVM image ARN. Pass image=..., set "
                "$AGENT_SANDBOX_IMAGE_ARN, or run `asb infra up`."
            )
        return resolved

    def _resolve_role(self, role: str | None) -> str:
        resolved = role or self._config.role_arn()
        if not resolved:
            raise SandboxError(
                "No execution role ARN. Pass role=..., set "
                "$AGENT_SANDBOX_EXECUTION_ROLE_ARN, or run `asb infra up`."
            )
        return resolved

    async def _create(
        self,
        name: str,
        *,
        image: str | None,
        role: str | None,
        region: str | None,
        cpus: int,
        memory: int,
        egress_connectors: list[str] | None,
    ) -> Sandbox:
        sandbox = await Sandbox.create(
            name,
            image_arn=self._resolve_image(image),
            cpus=cpus,
            memory=memory,
            execution_role_arn=self._resolve_role(role),
            region=region or self._config.region,
            egress_network_connectors=egress_connectors,
            verify_tls=self._config.verify_tls,
        )
        self._live[name] = sandbox
        await self._ensure_workdir(name, sandbox)
        return sandbox

    async def create_ephemeral(
        self,
        name: str,
        *,
        image: str | None = None,
        role: str | None = None,
        region: str | None = None,
        egress_connectors: list[str] | None = None,
    ) -> Sandbox:
        """Create a throwaway sandbox (not persisted to the state store)."""
        return await self._create(
            name, image=image, role=role, region=region,
            cpus=1, memory=512, egress_connectors=egress_connectors,
        )

    async def create_named(
        self,
        name: str,
        *,
        image: str | None = None,
        role: str | None = None,
        region: str | None = None,
        cpus: int = 1,
        memory: int = 512,
        egress_connectors: list[str] | None = None,
    ) -> Sandbox:
        """Create a persistent, named sandbox and record it in shared state."""
        sandbox = await self._create(
            name, image=image, role=role, region=region,
            cpus=cpus, memory=memory, egress_connectors=egress_connectors,
        )
        self.store.put(
            SandboxRecord(
                name=name,
                microvm_id=sandbox.microvm_id,
                endpoint=sandbox.endpoint,
                image_arn=self._resolve_image(image),
                region=region or self._config.region,
            )
        )
        return sandbox

    # -- access ------------------------------------------------------------

    async def get(self, name: str) -> Sandbox:
        """Return a live sandbox by name, re-attaching from state if needed."""
        if name in self._live:
            return self._live[name]
        rec = self.store.get(name)
        if rec is None:
            raise SandboxError(f"no sandbox named {name!r}")
        sandbox = await Sandbox.attach(
            name,
            rec.microvm_id,
            endpoint=rec.endpoint,
            region=rec.region or self._config.region,
            image_arn=rec.image_arn or "unknown",
            verify_tls=self._config.verify_tls,
        )
        self._live[name] = sandbox
        await self._ensure_workdir(name, sandbox)
        return sandbox

    def forget(self, name: str) -> None:
        """Drop a sandbox from the in-memory cache (leaves state untouched)."""
        self._live.pop(name, None)
        self._workdir_ready.discard(name)

    async def refresh_record(self, name: str) -> None:
        """Persist the current endpoint after a resume (endpoint can change)."""
        sandbox = self._live.get(name)
        rec = self.store.get(name)
        if sandbox is None or rec is None:
            return
        self.store.put(
            SandboxRecord(
                name=name,
                microvm_id=sandbox.microvm_id,
                endpoint=sandbox.endpoint,
                image_arn=rec.image_arn,
                region=rec.region,
            )
        )

    # -- shutdown ----------------------------------------------------------

    async def aclose(self) -> None:
        """Close cached agent HTTP clients (does not terminate MicroVMs)."""
        for sandbox in list(self._live.values()):
            agent = getattr(sandbox, "_agent", None)
            if agent is not None:
                try:
                    await agent.aclose()
                except Exception:  # noqa: BLE001 - shutdown is best-effort
                    pass
        self._live.clear()
