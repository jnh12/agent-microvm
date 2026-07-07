"""HTTP client for the in-VM guest agent (``agentd``).

The MicroVM exposes a dedicated HTTPS endpoint. Requests are authenticated with
a token minted by ``create_microvm_auth_token`` and sent in the
``X-aws-proxy-auth`` header. The token is refreshed lazily on a 401.

This mirrors microsandbox's ``agent-client``: a transport-focused client that
speaks the agent protocol, decoupled from the high-level ``Sandbox`` API.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable

import httpx

from agent_sandbox.errors import AgentError
from agent_sandbox.models import ExecResult

AUTH_HEADER = "X-aws-proxy-auth"

TokenProvider = Callable[[], Awaitable[str]]


class AgentClient:
    """Async client that talks to ``agentd`` inside a MicroVM."""

    def __init__(
        self,
        endpoint: str,
        token_provider: TokenProvider,
        *,
        timeout: float = 30.0,
        verify: bool = True,
        proxy_port: int | None = None,
    ) -> None:
        # The MicroVM endpoint is returned as a bare host; the proxy is
        # TLS-terminated, so default to https:// when no scheme is present.
        endpoint = endpoint.strip()
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        self._endpoint = endpoint.rstrip("/")
        self._token_provider = token_provider
        self._token: str | None = None
        # The proxy routes to port 8080 by default; override via X-aws-proxy-port.
        self._proxy_port = proxy_port
        self._client = httpx.AsyncClient(base_url=self._endpoint, timeout=timeout, verify=verify)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AgentClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _token_headers(self, force_refresh: bool = False) -> dict[str, str]:
        if self._token is None or force_refresh:
            self._token = await self._token_provider()
        headers = {AUTH_HEADER: self._token}
        if self._proxy_port is not None:
            headers["X-aws-proxy-port"] = str(self._proxy_port)
        return headers

    async def _post(self, path: str, json: dict) -> httpx.Response:
        headers = await self._token_headers()
        resp = await self._client.post(path, json=json, headers=headers)
        if resp.status_code == 401:
            headers = await self._token_headers(force_refresh=True)
            resp = await self._client.post(path, json=json, headers=headers)
        return resp

    async def health(self) -> bool:
        try:
            headers = await self._token_headers()
            resp = await self._client.get("/healthz", headers=headers)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def exec(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        payload: dict = {"command": command, "args": args or []}
        if cwd is not None:
            payload["cwd"] = cwd
        if env is not None:
            payload["env"] = env
        if timeout is not None:
            payload["timeout"] = timeout

        resp = await self._post("/v1/exec", payload)
        if resp.status_code != 200:
            raise AgentError(f"exec failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        return ExecResult(
            exit_code=int(data.get("exit_code", -1)),
            stdout=_decode_field(data, "stdout"),
            stderr=_decode_field(data, "stderr"),
        )

    async def read_file(self, path: str) -> bytes:
        resp = await self._post("/v1/fs/read", {"path": path})
        if resp.status_code != 200:
            raise AgentError(f"fs.read failed ({resp.status_code}): {resp.text}")
        return _decode_field(resp.json(), "content")

    async def write_file(self, path: str, content: bytes) -> None:
        payload = {
            "path": path,
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }
        resp = await self._post("/v1/fs/write", payload)
        if resp.status_code != 200:
            raise AgentError(f"fs.write failed ({resp.status_code}): {resp.text}")


def _decode_field(data: dict, key: str) -> bytes:
    """Decode a field that agentd may send as base64 (default) or plain text."""
    raw = data.get(key, "")
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    encoding = data.get("encoding", "base64")
    if encoding == "base64":
        try:
            return base64.b64decode(raw)
        except (ValueError, TypeError):
            return str(raw).encode("utf-8")
    return str(raw).encode("utf-8")
