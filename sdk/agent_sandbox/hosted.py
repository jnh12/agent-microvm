"""Authenticated transport for VMNest Cloud. No AWS credentials are used here."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from agent_sandbox.errors import ControlPlaneError

DEFAULT_API_URL = "https://vmnest.cc"
HOSTED_IMAGE_MESSAGE = (
    "Hosted computers use the preconfigured default image. "
    "Custom images require self-hosted mode."
)


def config_path() -> Path:
    return Path(os.environ.get("VMNEST_CONFIG", str(Path.home() / ".vmnest" / "auth.json")))


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    # DPAPI binds saved credentials to the Windows user, without an extra dependency.
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    fn = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Unable to access protected VMNest credentials")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def _saved() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise ValueError("Invalid saved login")
        if data.get("protected_token"):
            if os.name != "nt":
                raise ValueError("Windows-protected credential; run vmnest login on this computer")
            data["token"] = _protect(
                base64.b64decode(data["protected_token"]), decrypt=True
            ).decode()
        if not isinstance(data.get("token"), str) or not data["token"]:
            raise ValueError("Missing saved credential")
        return data
    except (OSError, ValueError, KeyError) as exc:
        raise ControlPlaneError("Cannot read VMNest login; run vmnest login again") from exc


def api_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ControlPlaneError("VMNest API URL must use HTTPS (HTTP is allowed only on localhost)")
    return value.rstrip("/")


def credentials() -> tuple[str, str] | None:
    if os.environ.get("VMNEST_MODE") == "self-hosted":
        return None
    if os.environ.get("VMNEST_API_TOKEN"):
        return api_url(os.environ.get("VMNEST_API_URL") or DEFAULT_API_URL), os.environ[
            "VMNEST_API_TOKEN"
        ]
    saved = _saved()
    url = api_url(os.environ.get("VMNEST_API_URL") or saved.get("url") or DEFAULT_API_URL)
    token = saved.get("token") if saved.get("url") == url else None
    if token:
        return url, token
    if os.environ.get("VMNEST_API_URL") or os.environ.get("VMNEST_MODE") == "hosted":
        raise ControlPlaneError("Sign in with vmnest login or set VMNEST_API_TOKEN")
    return None


def enabled() -> bool:
    return credentials() is not None


def save_login(url: str, token: str, account_id: str) -> None:
    data = {"url": api_url(url), "account_id": account_id}
    if os.name == "nt":
        data["protected_token"] = base64.b64encode(_protect(token.encode())).decode()
    else:
        data["token"] = token
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class HostedClient:
    def __init__(self, url: str | None = None, token: str | None = None):
        configured = credentials() if url is None else None
        self.url = api_url(url or (configured[0] if configured else DEFAULT_API_URL))
        self.token = token if token is not None else (configured[1] if configured else "")

    def request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with httpx.Client(timeout=180, follow_redirects=False) as client:
                response = client.request(method, self.url + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ControlPlaneError(f"VMNest request failed: {type(exc).__name__}") from exc
        if response.status_code == 428 and path == "/v1/auth/device/token":
            return None
        if not response.is_success:
            try:
                message = response.json().get("detail", response.text)
            except ValueError:
                message = response.text
            raise ControlPlaneError(f"VMNest ({response.status_code}): {str(message)[:500]}")
        return response.json() if response.content else {}

    def list(self, resource: str, **params) -> list[dict]:
        if resource == "images":
            return [self.image("default")]
        result = []
        seen = set()
        if resource == "sandboxes":
            params.update(limit=100, offset=0)
        while True:
            page = self.request("GET", "/v1/" + resource, params=params)
            if isinstance(page, list):
                result.extend(page)
                if resource != "sandboxes" or len(page) < params["limit"]:
                    return result
                params["offset"] += len(page)
                continue
            result.extend(page.get("items", page.get(resource, [])))
            cursor = page.get("next_cursor")
            if not cursor:
                return result
            if cursor in seen:
                raise ControlPlaneError("VMNest returned a repeated pagination cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    def sandbox(self, sandbox_id: str) -> dict:
        return self.request("GET", "/v1/sandboxes/" + quote(sandbox_id, safe=""))

    @staticmethod
    def info(item: dict) -> dict:
        return {
            **item,
            "microvmId": item["id"],
            "status": item["state"].upper(),
            "imageIdentifier": item.get("image", "default"),
        }

    def run_microvm(self, **kwargs) -> dict:
        if (
            kwargs.get("executionRoleArn")
            or kwargs.get("egressNetworkConnectors")
            or kwargs.get("ingressNetworkConnectors")
        ):
            raise ControlPlaneError(
                "AWS roles and network connectors are only available in self-hosted mode"
            )
        if kwargs.get("imageVersion") is not None or kwargs.get("imageIdentifier") not in (
            None, "default"
        ):
            raise ControlPlaneError(HOSTED_IMAGE_MESSAGE)
        body = {
            "name": kwargs.get("name") or "vmnest-" + uuid.uuid4().hex[:12],
            "duration": kwargs.get("maximumDurationInSeconds", 3600),
            "image": kwargs.get("imageIdentifier") or "default",
        }
        if kwargs.get("idlePolicy"):
            idle = kwargs["idlePolicy"]
            body["idle_policy"] = {
                "max_idle_duration_seconds": idle["maxIdleDurationSeconds"],
                "suspended_duration_seconds": idle["suspendedDurationSeconds"],
                "auto_resume_enabled": idle["autoResumeEnabled"],
            }
        return self.info(
            self.request(
                "POST", "/v1/sandboxes", json=body, headers={"Idempotency-Key": str(uuid.uuid4())}
            )
        )

    def get_microvm(self, microvmIdentifier: str) -> dict:
        return self.info(self.sandbox(microvmIdentifier))

    def list_microvms(self) -> dict:
        return {"microvms": [self.info(item) for item in self.list("sandboxes")]}

    def _state(self, sandbox_id: str, state: str) -> dict:
        result = self.request(
            "POST", "/v1/sandboxes/" + quote(sandbox_id, safe="") + "/state", json={"state": state}
        )
        if state == "running":
            return result
        deadline = time.monotonic() + 120
        while result["state"] != state:
            if result["state"] in {"terminated", "failed"}:
                if state == "terminated":
                    return result
                raise ControlPlaneError(f"Sandbox entered {result['state']}")
            if time.monotonic() >= deadline:
                raise ControlPlaneError(
                    f"Sandbox did not become {state} within 120s; check vmnest ps"
                )
            time.sleep(1)
            result = self.sandbox(sandbox_id)
        return result

    def suspend_microvm(self, microvmIdentifier: str) -> dict:
        return self._state(microvmIdentifier, "suspended")

    def resume_microvm(self, microvmIdentifier: str) -> dict:
        return self._state(microvmIdentifier, "running")

    def terminate_microvm(self, microvmIdentifier: str) -> dict:
        return self._state(microvmIdentifier, "terminated")

    def create_microvm_auth_token(self, microvmIdentifier: str, allowedPorts: list, **_) -> dict:
        if len(allowedPorts) != 1 or set(allowedPorts[0]) != {"port"}:
            raise ControlPlaneError("Hosted connections require exactly one explicit port")
        connection = self.request(
            "POST",
            "/v1/sandboxes/" + quote(microvmIdentifier, safe="") + "/connection",
            params={"port": allowedPorts[0]["port"]},
        )
        return {"authToken": {"X-aws-proxy-auth": connection["token"]}}

    def list_microvm_images(self) -> dict:
        return {"items": [self._image_info(item) for item in self.list("images")]}

    def list_managed_microvm_images(self) -> dict:
        return {"items": [self._image_info(item) for item in self.list("images", managed="true")]}

    @staticmethod
    def _image_info(item: dict) -> dict:
        return {
            **item,
            "imageArn": item["id"],
            "createdAt": item.get("created"),
            "latestActiveImageVersion": item.get("imageVersion")
            if item.get("state") == "ACTIVE"
            else None,
        }

    def delete_microvm_image(self, imageIdentifier: str) -> dict:
        raise ControlPlaneError(HOSTED_IMAGE_MESSAGE)

    def build_image(self, name: str, directory: str | Path) -> dict:
        """Hosted images are preconfigured; custom builds are self-hosted only."""
        raise ControlPlaneError(HOSTED_IMAGE_MESSAGE)

    def image(self, image_id: str) -> dict:
        if image_id != "default":
            raise ControlPlaneError(HOSTED_IMAGE_MESSAGE)
        return self.request("GET", "/v1/images/" + quote(image_id, safe=""))

    def wait_for_image(self, image_id: str, timeout: float = 900) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            image = self.image(image_id)
            state = image["state"].upper()
            if state == "ACTIVE":
                return image
            if state in {"FAILED", "DELETED"}:
                raise ControlPlaneError(f"Image {image_id} entered {state}")
            time.sleep(5)
        raise ControlPlaneError(f"Image {image_id} did not become active within {timeout}s")

    def create_microvm_image(self, **kwargs):
        raise ControlPlaneError(HOSTED_IMAGE_MESSAGE)

    def filter_log_events(self, *, logGroupName: str, **kwargs) -> dict:
        prefix = "/aws/lambda-microvms/"
        if not logGroupName.startswith(prefix):
            raise ControlPlaneError("Hosted logs can only read a sandbox's own log group")
        sandbox_id = logGroupName[len(prefix) :]
        params = {"limit": min(kwargs.get("limit", 100), 1000)}
        if "startTime" in kwargs:
            params["start_time"] = kwargs["startTime"]
        if "nextToken" in kwargs:
            params["cursor"] = kwargs["nextToken"]
        data = self.request(
            "GET", "/v1/sandboxes/" + quote(sandbox_id, safe="") + "/logs", params=params
        )
        return {"events": data["events"], "nextToken": data.get("next_cursor")}

    def get_metric_statistics(self, *, MetricName: str, Dimensions: list, **kwargs) -> dict:
        sandbox_id = next(d["Value"] for d in Dimensions if d["Name"] == "MicrovmId")
        data = self.request(
            "GET",
            "/v1/sandboxes/" + quote(sandbox_id, safe="") + "/metrics",
            params={"minutes": 30},
        )
        value = data.get(MetricName)
        return {
            "Datapoints": [] if value is None else [{"Average": value, "Timestamp": time.time()}]
        }
