"""Hosted agent profiles, disposable jobs and durable conversations."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from urllib.parse import quote

from agent_sandbox.errors import ControlPlaneError
from agent_sandbox.hosted import HostedClient

RESOURCES = {"credentials", "profiles", "connections", "agents", "runs", "webhooks"}
TERMINAL = {"succeeded", "failed", "cancelled"}


class Agents:
    """Use the same hosted login as ``vmnest``; no AWS credentials are needed.

    Credential secrets are write-only. Profiles explicitly select connections;
    an empty connection list grants none. A durable agent saves its conversation
    and workspace when stopped, while a disposable run cleans up automatically.
    """

    def __init__(self, url: str | None = None, token: str | None = None):
        self.client = HostedClient(url, token)
        if not self.client.token:
            raise ControlPlaneError("Sign in with vmnest login or set VMNEST_API_TOKEN")

    @staticmethod
    def _path(resource: str, resource_id: str | None = None) -> str:
        if resource not in RESOURCES:
            raise ValueError("Unknown agent resource")
        return "/v1/" + resource + ("/" + quote(resource_id, safe="") if resource_id else "")

    def list(self, resource: str, *, limit: int = 100, offset: int = 0) -> dict:
        return self.client.request(
            "GET", self._path(resource), params={"limit": limit, "offset": offset}
        )

    def get(self, resource: str, resource_id: str) -> dict:
        return self.client.request("GET", self._path(resource, resource_id))

    def create(self, resource: str, **fields) -> dict:
        try:
            return self.client.request(
                "POST",
                self._path(resource),
                json=fields,
                headers={"Idempotency-Key": str(uuid.uuid4())} if resource == "runs" else {},
            )
        except ControlPlaneError:
            if resource in {"credentials", "connections"}:
                raise ControlPlaneError(
                    "Unable to save provider settings; check the fields and your VMNest login"
                ) from None
            raise

    def update(self, resource: str, resource_id: str, **fields) -> dict:
        return self.client.request("PATCH", self._path(resource, resource_id), json=fields)

    def delete(self, resource: str, resource_id: str) -> dict:
        return self.client.request("DELETE", self._path(resource, resource_id))

    def action(self, resource: str, resource_id: str, action: str, **fields) -> dict:
        actions = {
            "credentials": {"verify", "connections/discover"},
            "agents": {"start", "stop"},
            "runs": {"cancel"},
            "webhooks": {"rotate", "test"},
        }
        if action not in actions.get(resource, set()):
            raise ValueError("Unknown resource action")
        return self.client.request(
            "POST", self._path(resource, resource_id) + "/" + action, json=fields
        )

    def run(
        self,
        prompt: str,
        *,
        profile_id: str | None = None,
        agent_id: str | None = None,
        name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if bool(profile_id) == bool(agent_id):
            raise ValueError("Provide exactly one of profile_id and agent_id")
        body = {"prompt": prompt}
        body.update(
            {
                k: v
                for k, v in {"profile_id": profile_id, "agent_id": agent_id, "name": name}.items()
                if v is not None
            }
        )
        return self.client.request(
            "POST",
            "/v1/runs",
            json=body,
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )

    def message(self, agent_id: str, text: str, *, idempotency_key: str | None = None) -> dict:
        return self.client.request(
            "POST",
            self._path("agents", agent_id) + "/messages",
            json={"text": text},
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
        )

    def messages(self, agent_id: str) -> dict:
        return self.client.request("GET", self._path("agents", agent_id) + "/messages")

    def events(self, run_id: str, *, after: int = 0) -> dict:
        return self.client.request(
            "GET", self._path("runs", run_id) + "/events", params={"after": after}
        )

    def follow(
        self, run_id: str, *, after: int = 0, poll_interval: float = 1, timeout: float = 7200
    ) -> Iterator[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            page = self.events(run_id, after=after)
            for event in page.get("items", []):
                if event["id"] > after:
                    after = event["id"]
                    yield event
            after = max(after, page.get("next_cursor", after) or after)
            if page.get("complete"):
                return
            time.sleep(max(0.1, poll_interval))
        raise TimeoutError(f"Run {run_id} is still active; reconnect with vmnest run events")

    def artifacts(self, run_id: str) -> dict:
        return self.client.request("GET", self._path("runs", run_id) + "/artifacts")

    def artifact_url(self, run_id: str, artifact_id: str) -> dict:
        return self.client.request(
            "GET",
            self._path("runs", run_id) + "/artifacts/" + quote(artifact_id, safe="") + "/download",
        )

    def connection_tools(self, connection_id: str) -> dict:
        return self.client.request("GET", self._path("connections", connection_id) + "/tools")
