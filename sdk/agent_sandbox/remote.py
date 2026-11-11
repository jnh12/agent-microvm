"""Transport for a single-user VM backend. No account or AWS credentials required."""

import os
from urllib.parse import quote, urlsplit

import httpx

from .errors import SandboxError


def remote_url():
    return os.environ.get("VMNEST_API_URL", "").rstrip("/")


def request(method, path, body=None):
    url = remote_url()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SandboxError("Set VMNEST_API_URL to the backend URL")
    try:
        with httpx.Client(timeout=httpx.Timeout(None, connect=10), trust_env=False) as client:
            response = client.request(method, url + path, json=body)
        if response.is_error:
            raise SandboxError(f"Remote operation failed ({response.status_code}): {response.text}")
        return response.json()
    except httpx.HTTPError:
        raise SandboxError(
            "Backend connection failed; operation may have completed. No retry made."
        ) from None


def vm_path(name):
    return "/v1/vms/" + quote(name, safe="")


class ForwardConnection:
    """Supply only the VM status and port-scoped grants the existing proxy needs."""

    def __init__(self, name):
        self.name = name

    async def get_microvm(self, identifier):
        import asyncio

        from .control_plane import MicroVMInfo

        row = await asyncio.to_thread(request, "GET", vm_path(self.name))
        return MicroVMInfo(row["id"], row["state"], row.get("endpoint"), row)

    async def create_auth_token(self, identifier, expiration_in_minutes=30, allowed_ports=None):
        import asyncio

        port = allowed_ports[0]["port"]
        result = await asyncio.to_thread(request, "POST", vm_path(self.name) + f"/forward/{port}")
        return result["token"]
