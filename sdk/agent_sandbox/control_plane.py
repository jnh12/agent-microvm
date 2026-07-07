"""Thin async wrapper over the AWS ``lambda-microvms`` control plane.

boto3 is synchronous, so every call is dispatched to a worker thread via
``asyncio.to_thread`` to keep the SDK's public surface async.

The ``lambda-microvms`` service is very new; response shapes are still
stabilizing. Field extraction is therefore defensive and tries a few likely
key spellings before giving up.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "boto3 is required for the control plane. Install with `pip install boto3`."
    ) from exc

from agent_sandbox.errors import ControlPlaneError, SandboxTimeoutError

logger = logging.getLogger(__name__)

_SERVICE = "lambda-microvms"

# The lambda-microvms service is new and occasionally returns a transient
# ResourceNotFoundException (e.g. eventual consistency right after rapid
# create/terminate cycles) even when the image version is ACTIVE. These codes
# are safe to retry with backoff for idempotent-ish launch operations.
_TRANSIENT_ERROR_CODES = frozenset(
    {"ResourceNotFoundException", "ThrottlingException", "TooManyRequestsException"}
)


def _client_error_code(exc: BaseException) -> str | None:
    """Extract the AWS error code from a botocore ``ClientError`` (or None)."""
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code")
    return None


def _first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


@dataclass(slots=True)
class MicroVMInfo:
    """Normalized view of a MicroVM returned by the control plane."""

    microvm_id: str
    status: str | None
    endpoint: str | None
    raw: dict[str, Any]

    @classmethod
    def from_response(cls, resp: dict[str, Any]) -> MicroVMInfo:
        body = _first(resp, "microvm", "microVm", "MicroVm") or resp
        microvm_id = _first(body, "microvmId", "microVmId", "id", "MicrovmId")
        status = _first(body, "status", "state", "Status")
        endpoint = _first(body, "endpoint", "endpointUrl", "url", "Url", "EndpointUrl")
        if not microvm_id:
            raise ControlPlaneError(f"Could not find a MicroVM id in response: {resp!r}")
        return cls(microvm_id=microvm_id, status=status, endpoint=endpoint, raw=body)


class ControlPlane:
    """Async facade over boto3's ``lambda-microvms`` client."""

    def __init__(self, region: str | None = None, client: Any | None = None) -> None:
        self._region = region
        if client is not None:
            self._client = client
        else:
            try:
                self._client = boto3.client(_SERVICE, region_name=region)
            except Exception as exc:  # noqa: BLE001 - surface as ControlPlaneError
                raise ControlPlaneError(
                    f"Failed to create '{_SERVICE}' client. Ensure AWS CLI v2 is recent "
                    f"enough and the service is available in your region: {exc}"
                ) from exc

    async def _call(
        self,
        op: str,
        *,
        retry_timeout: float = 0.0,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 10.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Invoke a boto op, optionally retrying transient errors.

        ``retry_timeout`` is a *wall-clock* budget (seconds): transient errors
        (see ``_TRANSIENT_ERROR_CODES``) are retried with capped-exponential
        backoff + jitter until the budget is exhausted, then re-raised. The
        lambda-microvms preview service can flap "No active version" for tens of
        seconds even while the image is ACTIVE, so a time budget rides those out.
        """
        fn = getattr(self._client, op, None)
        if fn is None:
            raise ControlPlaneError(
                f"boto3 '{_SERVICE}' client has no operation '{op}'. "
                "Your botocore/AWS CLI may be too old for Lambda MicroVMs."
            )
        attempt = 0
        start = time.monotonic()
        while True:
            try:
                return await asyncio.to_thread(fn, **kwargs)
            except (ClientError, BotoCoreError) as exc:
                code = _client_error_code(exc)
                elapsed = time.monotonic() - start
                if (
                    retry_timeout > 0
                    and elapsed < retry_timeout
                    and code in _TRANSIENT_ERROR_CODES
                ):
                    # Capped exponential backoff with jitter: ~1s, 2s, 4s, 8s, 10s...
                    delay = min(retry_base_delay * (2**attempt), retry_max_delay)
                    delay += random.uniform(0, delay * 0.25)
                    logger.warning(
                        "%s hit transient %s; retrying in %.1fs "
                        "(elapsed %.0fs / %.0fs budget)",
                        op,
                        code,
                        delay,
                        elapsed,
                        retry_timeout,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise ControlPlaneError(f"{op} failed: {exc}") from exc

    # -- lifecycle ---------------------------------------------------------

    async def run_microvm(
        self,
        image_arn: str,
        execution_role_arn: str | None = None,
        idle_policy: dict[str, Any] | None = None,
        image_version: str | None = None,
        ingress_network_connectors: list[str] | None = None,
        egress_network_connectors: list[str] | None = None,
        maximum_duration_seconds: int | None = None,
    ) -> MicroVMInfo:
        # CPU/memory are image-level (set at image build via cpuConfigurations /
        # resources), not run-time. Networking uses network *connectors*, not
        # subnets/security groups. Only valid RunMicrovm params are sent.
        kwargs: dict[str, Any] = {"imageIdentifier": image_arn}
        if execution_role_arn:
            kwargs["executionRoleArn"] = execution_role_arn
        if idle_policy:
            kwargs["idlePolicy"] = idle_policy
        if image_version:
            kwargs["imageVersion"] = image_version
        if ingress_network_connectors:
            kwargs["ingressNetworkConnectors"] = ingress_network_connectors
        if egress_network_connectors:
            kwargs["egressNetworkConnectors"] = egress_network_connectors
        if maximum_duration_seconds is not None:
            kwargs["maximumDurationInSeconds"] = maximum_duration_seconds
        # Retry transient launch failures (see _TRANSIENT_ERROR_CODES). The
        # preview service intermittently reports "No active version" for
        # ~30-60s windows even while the image version is ACTIVE; a wall-clock
        # budget rides those out. Failed attempts create no MicroVM, so retrying
        # cannot orphan resources.
        return MicroVMInfo.from_response(
            await self._call("run_microvm", retry_timeout=180.0, **kwargs)
        )

    async def get_microvm(self, microvm_id: str) -> MicroVMInfo:
        resp = await self._call("get_microvm", microvmIdentifier=microvm_id)
        return MicroVMInfo.from_response(resp)

    async def suspend_microvm(self, microvm_id: str) -> None:
        await self._call("suspend_microvm", microvmIdentifier=microvm_id)

    async def resume_microvm(self, microvm_id: str) -> None:
        await self._call("resume_microvm", microvmIdentifier=microvm_id)

    async def terminate_microvm(self, microvm_id: str) -> None:
        await self._call("terminate_microvm", microvmIdentifier=microvm_id)

    async def list_microvms(self) -> list[MicroVMInfo]:
        resp = await self._call("list_microvms")
        items = _first(resp, "microvms", "microVms", "MicroVms", "items") or []
        out: list[MicroVMInfo] = []
        for item in items:
            try:
                out.append(MicroVMInfo.from_response({"microvm": item}))
            except ControlPlaneError:
                continue
        return out

    async def create_auth_token(
        self,
        microvm_id: str,
        expiration_in_minutes: int = 30,
        allowed_ports: list[dict[str, Any]] | None = None,
    ) -> str:
        resp = await self._call(
            "create_microvm_auth_token",
            microvmIdentifier=microvm_id,
            expirationInMinutes=expiration_in_minutes,
            allowedPorts=allowed_ports or [{"port": 8080}],
        )
        # authToken is a header map, e.g. {"X-aws-proxy-auth": "<token>"}.
        token_map = _first(resp, "authToken", "AuthToken") or {}
        token = None
        if isinstance(token_map, dict):
            token = token_map.get("X-aws-proxy-auth") or (
                next(iter(token_map.values()), None) if token_map else None
            )
        elif isinstance(token_map, str):
            token = token_map
        if not token:
            raise ControlPlaneError(f"No auth token in response: {resp!r}")
        return token

    async def wait_until_running(
        self, microvm_id: str, timeout: float = 120.0, poll_interval: float = 1.0
    ) -> MicroVMInfo:
        deadline = time.monotonic() + timeout
        last: MicroVMInfo | None = None
        while time.monotonic() < deadline:
            last = await self.get_microvm(microvm_id)
            status = (last.status or "").upper()
            if status == "RUNNING":
                return last
            if status in {"FAILED", "TERMINATED"}:
                raise ControlPlaneError(
                    f"MicroVM {microvm_id} entered terminal status {status}"
                )
            await asyncio.sleep(poll_interval)
        raise SandboxTimeoutError(
            f"MicroVM {microvm_id} did not reach RUNNING within {timeout}s "
            f"(last status: {last.status if last else 'unknown'})"
        )

    # -- images ------------------------------------------------------------

    async def create_microvm_image(
        self, name: str, s3_bucket: str, s3_key: str, execution_role_arn: str | None = None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "imageName": name,
            "code": {"s3Bucket": s3_bucket, "s3Key": s3_key},
        }
        if execution_role_arn:
            kwargs["executionRoleArn"] = execution_role_arn
        return await self._call("create_microvm_image", **kwargs)

    async def list_microvm_images(self) -> list[dict[str, Any]]:
        """List this account's own MicroVM images (not the managed base images)."""
        resp = await self._call("list_microvm_images")
        return _first(resp, "items", "images", "microvmImages", "Images") or []

    async def list_managed_microvm_images(self) -> list[dict[str, Any]]:
        """List the AWS-provided managed base images (e.g. al2023)."""
        resp = await self._call("list_managed_microvm_images")
        return _first(resp, "items", "images", "microvmImages", "Images") or []

    async def delete_microvm_image(self, image_arn: str) -> None:
        await self._call("delete_microvm_image", imageIdentifier=image_arn)
