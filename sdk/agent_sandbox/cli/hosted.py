"""Hosted lifecycle routing for the existing CLI commands."""

import json
import time
import uuid
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountClient, AccountError, mode, service_url

from .accounts import fail


def route(ctx, action, *, name=None, request_key=None, as_json=False):
    """Return True when handled remotely; never read local AWS sandbox state."""
    try:
        if mode() != "hosted":
            return False
        for option in ("image", "role", "region", "cpus", "memory", "egress", "egress_connector"):
            if getattr(ctx.get_parameter_source(option), "name", None) == "COMMANDLINE":
                raise AccountError(
                    "HOSTED_OPTION_UNSUPPORTED",
                    f"--{option.replace('_', '-')} is managed by the hosted service.",
                )
        client = AccountClient(service_url())
        path = "/v1/vms" + ("/" + quote(name, safe="") if name else "")
        if action == "create":
            key = request_key or str(uuid.uuid4())
            try:
                # A timeout may follow a successful launch: retry only with the same key.
                for attempt in range(2):
                    try:
                        result = client.authenticated(
                            "POST", "/v1/vms", body={"name": name}, request_key=key
                        )
                        break
                    except AccountError as exc:
                        if exc.code != "SERVICE_UNAVAILABLE" or attempt:
                            raise
                result["request_key"] = key
            except AccountError as exc:
                if exc.code == "SERVICE_UNAVAILABLE":
                    raise AccountError(
                        exc.code,
                        f"Request outcome unknown. Retry: vmnest create {name} --request-key {key}",
                    ) from None
                raise
        elif action == "rm":
            result = client.authenticated("DELETE", path)
        else:
            result = client.authenticated("GET", path)
        # Human commands wait briefly; JSON commands return immediately for agent polling.
        if not as_json and action in {"create", "rm"}:
            deadline = time.monotonic() + 30
            while result["state"] in {"PENDING", "TERMINATING"} and time.monotonic() < deadline:
                time.sleep(1)
                result = client.authenticated("GET", "/v1/vms/" + result["id"])
        if as_json or action == "inspect":
            typer.echo(json.dumps(result))
        elif action == "ls":
            rows = result["items"]
            typer.echo(
                "\n".join(f"{r['name']}  {r['state']}  {r['id']}" for r in rows)
                if rows
                else "No VMs."
            )
        else:
            typer.echo(f"{result['name']}: {result['state']} ({result['id']})")
        if action == "create" and result["state"] in {"FAILED", "UNKNOWN"}:
            raise typer.Exit(1)
        return True
    except AccountError as exc:
        fail(exc, as_json)
