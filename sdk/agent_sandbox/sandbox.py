"""High-level :class:`Sandbox` API.

Mirrors microsandbox's embeddable SDK ergonomics::

    sandbox = await Sandbox.create("my-sandbox", image_arn=..., cpus=1, memory=512)
    result = await sandbox.exec("python", ["-c", "print('hi')"])
    print(result.stdout_text)
    await sandbox.stop()

Under the hood a ``Sandbox`` owns a MicroVM (via :class:`ControlPlane`) and an
:class:`AgentClient` connected to that MicroVM's HTTPS endpoint.
"""

from __future__ import annotations

import os
from typing import Any

from agent_sandbox.agent_client import AgentClient
from agent_sandbox.control_plane import ControlPlane, MicroVMInfo
from agent_sandbox.errors import SandboxError
from agent_sandbox.models import ExecResult, IdlePolicy, SandboxConfig, SandboxState
from agent_sandbox.ports import AGENT_PORT

ENV_IMAGE_ARN = "AGENT_SANDBOX_IMAGE_ARN"
ENV_ROLE_ARN = "AGENT_SANDBOX_EXECUTION_ROLE_ARN"


class Sandbox:
    """An isolated microVM sandbox backed by AWS Lambda MicroVMs."""

    def __init__(
        self,
        config: SandboxConfig,
        control_plane: ControlPlane,
        info: MicroVMInfo,
        agent: AgentClient | None = None,
    ) -> None:
        self._config = config
        self._cp = control_plane
        self._info = info
        self._agent = agent

    # -- properties --------------------------------------------------------

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def microvm_id(self) -> str:
        return self._info.microvm_id

    @property
    def endpoint(self) -> str | None:
        return self._info.endpoint

    @property
    def state(self) -> SandboxState:
        return SandboxState.from_aws_status(self._info.status)

    # -- construction ------------------------------------------------------

    @classmethod
    async def create(
        cls,
        name: str,
        *,
        image_arn: str | None = None,
        cpus: int = 1,
        memory: int = 512,
        execution_role_arn: str | None = None,
        region: str | None = None,
        idle_policy: IdlePolicy | None = None,
        agent_port: int = AGENT_PORT,
        image_version: str | None = None,
        ingress_network_connectors: list[str] | None = None,
        egress_network_connectors: list[str] | None = None,
        wait_timeout: float = 120.0,
        verify_tls: bool = True,
    ) -> Sandbox:
        """Launch a new MicroVM and wait until its guest agent is reachable.

        Note: ``cpus``/``memory`` are image-level on Lambda MicroVMs (fixed at
        image build time), so they configure the SDK record only and are not sent
        to ``run_microvm``. Networking uses network connectors, not subnets/SGs.
        """
        image_arn = image_arn or os.environ.get(ENV_IMAGE_ARN)
        execution_role_arn = execution_role_arn or os.environ.get(ENV_ROLE_ARN)
        if not image_arn:
            raise SandboxError(
                f"image_arn is required (pass it or set ${ENV_IMAGE_ARN})."
            )
        if not execution_role_arn:
            raise SandboxError(
                f"execution_role_arn is required (pass it or set ${ENV_ROLE_ARN})."
            )

        config = SandboxConfig(
            name=name,
            image_arn=image_arn,
            cpus=cpus,
            memory=memory,
            execution_role_arn=execution_role_arn,
            region=region,
            idle_policy=idle_policy or IdlePolicy(),
            agent_port=agent_port,
            ingress_network_connectors=ingress_network_connectors or [],
            egress_network_connectors=egress_network_connectors or [],
        )
        cp = ControlPlane(region=region)
        info = await cp.run_microvm(
            image_arn=config.image_arn,
            execution_role_arn=config.execution_role_arn,
            idle_policy=config.idle_policy.to_aws(),
            image_version=image_version,
            ingress_network_connectors=config.ingress_network_connectors or None,
            egress_network_connectors=config.egress_network_connectors or None,
        )
        info = await cp.wait_until_running(info.microvm_id, timeout=wait_timeout)
        sandbox = cls(config=config, control_plane=cp, info=info)
        sandbox._connect_agent(verify_tls=verify_tls)
        return sandbox

    @classmethod
    async def attach(
        cls,
        name: str,
        microvm_id: str,
        *,
        endpoint: str | None = None,
        region: str | None = None,
        image_arn: str = "unknown",
        execution_role_arn: str = "unknown",
        verify_tls: bool = True,
    ) -> Sandbox:
        """Re-attach to an existing MicroVM (used by the CLI's state store)."""
        cp = ControlPlane(region=region)
        info = await cp.get_microvm(microvm_id)
        if endpoint and not info.endpoint:
            info.endpoint = endpoint
        config = SandboxConfig(
            name=name,
            image_arn=image_arn,
            execution_role_arn=execution_role_arn,
            region=region,
        )
        sandbox = cls(config=config, control_plane=cp, info=info)
        if info.endpoint:
            sandbox._connect_agent(verify_tls=verify_tls)
        return sandbox

    def _connect_agent(self, *, verify_tls: bool = True) -> None:
        if not self._info.endpoint:
            raise SandboxError(
                f"MicroVM {self.microvm_id} has no endpoint URL; cannot connect agent."
            )

        async def token_provider() -> str:
            return await self._cp.create_auth_token(
                self.microvm_id, allowed_ports=[{"port": self._config.agent_port}]
            )

        self._agent = AgentClient(
            self._info.endpoint,
            token_provider,
            verify=verify_tls,
            proxy_port=self._config.agent_port,
        )

    def _require_agent(self) -> AgentClient:
        if self._agent is None:
            raise SandboxError(
                "Agent is not connected. The MicroVM may be suspended or lacks an endpoint."
            )
        return self._agent

    # -- operations --------------------------------------------------------

    async def exec(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        return await self._require_agent().exec(
            command, args, cwd=cwd, env=env, timeout=timeout
        )

    async def read_file(self, path: str) -> bytes:
        return await self._require_agent().read_file(path)

    async def write_file(self, path: str, content: bytes) -> None:
        await self._require_agent().write_file(path, content)

    async def refresh(self) -> SandboxState:
        self._info = await self._cp.get_microvm(self.microvm_id)
        return self.state

    async def inspect(self) -> dict[str, Any]:
        info = await self._cp.get_microvm(self.microvm_id)
        self._info = info
        return info.raw

    async def suspend(self) -> None:
        await self._cp.suspend_microvm(self.microvm_id)

    async def start(self) -> None:
        """Resume a suspended MicroVM and reconnect the agent."""
        await self._cp.resume_microvm(self.microvm_id)
        self._info = await self._cp.wait_until_running(self.microvm_id)
        self._connect_agent()

    resume = start

    async def stop(self) -> None:
        """Tear down the sandbox (terminates the MicroVM), mirroring microsandbox."""
        await self.terminate()

    async def terminate(self) -> None:
        if self._agent is not None:
            await self._agent.aclose()
            self._agent = None
        await self._cp.terminate_microvm(self.microvm_id)

    kill = terminate

    async def __aenter__(self) -> Sandbox:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()
