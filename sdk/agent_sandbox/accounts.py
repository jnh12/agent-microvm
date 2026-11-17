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


def public_account(value):
    if not isinstance(value, dict):
        raise AccountError("SERVICE_ERROR", "Invalid account response.")
    return {
        key: value[key]
        for key in ("id", "github_id", "login", "created_at", "expires_at", "is_admin")
        if key in value
    }


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
    value = os.environ.get("VMNEST_MODE") or (
        "hosted" if os.environ.get("VMNEST_API_URL") else read_config().get("mode", "hosted")
    )
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
    value = (
        override
        or os.environ.get("VMNEST_API_URL")
        or os.environ.get("VMNEST_URL")
        or read_config().get("url")
        or "https://api.vmnest.cc"
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
        timeout: float = 20,
    ):
        headers = {
            "X-VMNest-Client": "cli",
            **({"Authorization": "Bearer " + token} if token else {}),
        }
        if request_key:
            headers["Idempotency-Key"] = request_key
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.request(method, self.url + path, headers=headers, json=body)
            if response.status_code >= 300:
                try:
                    error = response.json()["error"]
                    code = error["code"]
                except (ValueError, KeyError, TypeError):
                    code = "SERVICE_ERROR"
                # Avoid printing arbitrary provider/proxy responses or secrets.
                messages = {
                    "LOGIN_UNAVAILABLE": "Provider sign-in is temporarily unavailable. Try again later.",
                    "LOGIN_LIMIT": "Too many pending sign-ins. Try again shortly.",
                    "LOGIN_NOT_FOUND": "Sign-in expired or was lost after a restart. Start again.",
                    "LOGIN_NOT_PENDING": "This sign-in is not waiting for an authorization code.",
                    "HOURS_EXHAUSTED": (
                        "Monthly machine-hours are used up. Upgrade at "
                        "vmnest.cc/dashboard/billing or wait for renewal."
                    ),
                    "MACHINE_LIMIT": (
                        "Your plan's machine limit is reached. Delete a machine or upgrade."
                    ),
                    "BILLING_UNAVAILABLE": "Billing is temporarily unavailable. Please retry.",
                    "NO_SUBSCRIPTION": "Choose a plan on the VMNest billing page.",
                    "SUBSCRIPTION_EXISTS": (
                        "You already have a subscription. Manage it on the VMNest billing page."
                    ),
                    "BILLING_PENDING": "Payment is being confirmed. Run vmnest usage shortly.",
                    "BILLING_REVIEW": "Your subscriptions need review. Contact VMNest support.",
                    "AGENT_BUSY": "The agent is still working, or its queue is paused.",
                    "VM_BUSY": "The computer is busy with another operation. Try again shortly.",
                    "VM_SAVING": "The computer is saving or waking. Try again shortly.",
                    "VM_NOT_RUNNING": "The computer is not running. Open it and try again.",
                    "AGENT_NOT_READY": "The agent is not ready yet. Try again shortly.",
                    "PROJECT_NOT_READY": "Project setup is not ready yet.",
                    "COMPUTER_STARTING": (
                        "The computer is still waking. Retry with the same request key."
                    ),
                    "REQUEST_CONFLICT": "This request key was already used for different input.",
                    "DISCOVERY_UNAVAILABLE": (
                        "Integration discovery is unavailable; retry later or contact the operator."
                    ),
                    "CONNECTION_CHANGED": "The agent connection changed. Try again.",
                    "AGENT_NOT_CONNECTED": "Connect your Codex account first.",
                    "AGENT_RECONNECT_REQUIRED": "Reconnect the agent account with --replace.",
                    "AGENT_REFRESH_PENDING": "Agent refresh is pending. Try again later.",
                    "INTEGRATION_UNAVAILABLE": (
                        "A selected integration is unavailable. Check vmnest agent integrations."
                    ),
                    "PROFILE_NOT_FOUND": "Agent profile not found.",
                    "ENDPOINT_NOT_FOUND": "Event endpoint not found.",
                    "ENDPOINT_EXISTS": "An event endpoint already uses this name.",
                    "ENDPOINT_DISABLED": "Enable the event endpoint before sending a test.",
                    "ENDPOINT_LIMIT": "At most 20 event endpoints are allowed per account.",
                    "PROFILE_EXISTS": "Agent profile exists. Use profile update.",
                    "CONNECTION_EXISTS": (
                        "A connection is already saved. Use --replace to explicitly replace it."
                    ),
                    "INVALID_REQUEST": "Unsupported or invalid request data.",
                    "INVALID_DEFAULTS": (
                        "Invalid cloud defaults. Check variable names, reserved runtime names, "
                        "and size limits."
                    ),
                    "DEFAULTS_CHANGED": (
                        "Defaults changed in another session. Review them with "
                        "vmnest defaults list before retrying."
                    ),
                    "WEBHOOK_ACTION_FORBIDDEN": "This action is not enabled for this webhook.",
                    "WEBHOOK_LIMIT": "At most 50 active machine webhooks are allowed.",
                    "WEBHOOK_TIMEOUT": "Delivery timed out before dispatch.",
                    "WEBHOOK_NOT_FOUND": "Webhook not found. Use vmnest webhook list.",
                    "WEBHOOK_REVOKED": "This webhook is revoked. Create a replacement webhook.",
                    "WEBHOOK_EXISTS": "Webhook name already exists. Choose another name.",
                    "WEBHOOK_RUN_NOT_FOUND": "Webhook run not found.",
                    "WEBHOOK_AUTH_REQUIRED": "Webhook credentials are invalid or revoked.",
                    "WEBHOOK_QUEUE_FULL": "The webhook queue is full. Retry later.",
                    "PAYLOAD_TOO_LARGE": "The request exceeds the service size limit.",
                    "AUTH_REQUIRED": "Sign in with vmnest login.",
                    "AUTH_EXPIRED": "Session invalid or revoked. Run vmnest login.",
                    "LOGIN_EXPIRED": "Login expired. Run vmnest login again.",
                    "LOGIN_LOCKED": "Too many incorrect terminal codes. Run vmnest login again.",
                    "LOGIN_DENIED": "GitHub login denied. Run vmnest login again.",
                    "LOGIN_INVALID": "Could not verify login. Run vmnest login again.",
                    "RATE_LIMITED": "Too many login attempts. Try again later.",
                    "LOGIN_NOT_CONFIGURED": "The server needs GitHub login configured. "
                    "Use vmnest login --device for the device-code fallback.",
                    "PROVIDER_UNAVAILABLE": "GitHub is unavailable. Try again shortly.",
                }
                if code not in messages:
                    code = "SERVICE_ERROR"
                raise AccountError(code, messages.get(code, "Account service request failed."))
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            raise AccountError("SERVICE_UNAVAILABLE", "Cannot reach the account service.") from None

    def account(self):
        return public_account(self.authenticated("GET", "/v1/account"))

    def authenticated(self, method, path, **kwargs):
        token = secret(self.url, "session")
        if not token:
            raise AccountError("AUTH_REQUIRED", "Sign in with vmnest login.")
        return self.request(method, path, token=token, **kwargs)
