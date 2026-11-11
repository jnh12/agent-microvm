"""Hosted account client. Independent of the direct-AWS VM runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import keyring


class AccountError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def config_path() -> Path:
    return Path(os.environ.get("VMNEST_CONFIG", str(Path.home() / ".vmnest" / "config.json")))


def read_config() -> dict:
    try:
        value = json.loads(config_path().read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise AccountError("CONFIG_INVALID", "Cannot read VMNest configuration.") from None


def save_config(value: dict) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)
    except OSError:
        raise AccountError("CONFIG_UNWRITABLE", "Cannot save VMNest configuration.") from None


def mode() -> str | None:
    value = os.environ.get("VMNEST_MODE") or read_config().get("mode")
    if value not in (None, "hosted", "self-hosted"):
        raise AccountError("CONFIG_INVALID", "VMNEST_MODE must be hosted or self-hosted.")
    return value


def normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise AccountError("URL_INVALID", "Invalid account service URL.") from None
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or (parts.scheme != "https" and not local)
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise AccountError("URL_INVALID", "Use an HTTPS origin; HTTP is allowed only on loopback.")
    return value.rstrip("/")


def service_url(override: str | None = None) -> str:
    value = override or os.environ.get("VMNEST_URL") or read_config().get("url")
    if not value:
        raise AccountError(
            "SERVICE_NOT_CONFIGURED",
            "Run vmnest setup --mode hosted --url https://YOUR-ACCOUNT-SERVICE",
        )
    return normalize_url(value)


def credential_store():
    backend = keyring.get_keyring()
    # Never silently fall back to a plaintext file or an arbitrary plugin backend.
    allowed = (
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.kwallet",
    )
    if not type(backend).__module__.startswith(allowed):
        raise AccountError(
            "CREDENTIAL_STORE_UNAVAILABLE",
            "Unlock an OS credential store, then run vmnest login.",
        )
    return backend


def secret(url: str, slot: str, value: str | None = None, *, delete: bool = False):
    try:
        store = credential_store()
        service = "vmnest:" + url
        if delete:
            if store.get_password(service, slot) is not None:
                store.delete_password(service, slot)
            return None
        if value is not None:
            store.set_password(service, slot, value)
            return None
        return store.get_password(service, slot)
    except AccountError:
        raise
    except Exception:
        raise AccountError(
            "CREDENTIAL_STORE_UNAVAILABLE", "The OS credential store is locked or unavailable."
        ) from None


class AccountClient:
    def __init__(self, url: str):
        self.url = normalize_url(url)

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body=None,
        request_key: str | None = None,
    ):
        headers = {"Authorization": "Bearer " + token} if token else {}
        if request_key:
            headers["Idempotency-Key"] = request_key
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.request(method, self.url + path, headers=headers, json=body)
            if response.status_code >= 300:
                try:
                    error = response.json()["error"]
                    code = error["code"]
                except (ValueError, KeyError, TypeError):
                    code = "SERVICE_ERROR"
                # Avoid printing arbitrary provider/proxy responses or secrets.
                messages = {
                    "AUTH_REQUIRED": "Sign in with vmnest login.",
                    "AUTH_EXPIRED": "Session invalid or revoked. Run vmnest login.",
                    "LOGIN_EXPIRED": "Login expired. Run vmnest login again.",
                    "LOGIN_DENIED": "GitHub login denied. Run vmnest login again.",
                    "LOGIN_INVALID": "Could not verify login. Run vmnest login again.",
                    "RATE_LIMITED": "Too many login attempts. Try again later.",
                    "LOGIN_NOT_CONFIGURED": "The server needs GitHub login configured. "
                    "Use vmnest login --device for the device-code fallback.",
                    "PROVIDER_UNAVAILABLE": "GitHub is unavailable. Try again shortly.",
                    "VM_NOT_FOUND": "VM not found in your account.",
                    "VM_LIMIT_REACHED": (
                        "Your active VM limit is reached. Terminate a VM before creating another."
                    ),
                    "REQUEST_CONFLICT": "This request key belongs to another VM name.",
                    "HOSTED_VMS_UNAVAILABLE": "The server has not enabled hosted VMs.",
                    "INVALID_REQUEST": "Invalid request. Check the name and supported options.",
                }
                if code not in messages:
                    code = "SERVICE_ERROR"
                raise AccountError(code, messages.get(code, "Account service request failed."))
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            raise AccountError("SERVICE_UNAVAILABLE", "Cannot reach the account service.") from None

    def account(self):
        return self.authenticated("GET", "/v1/account")

    def authenticated(self, method, path, **kwargs):
        token = secret(self.url, "session")
        if not token:
            raise AccountError("AUTH_REQUIRED", "Sign in with vmnest login.")
        return self.request(method, path, token=token, **kwargs)
