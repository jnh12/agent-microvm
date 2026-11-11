"""Hosted lifecycle routing for the existing CLI commands."""

import base64
import json
import os
import time
import uuid
from urllib.parse import quote

import typer

from agent_sandbox.accounts import AccountClient, AccountError, mode, service_url

from .accounts import fail


def execute(ctx, name, command, *, cwd=None, env=None, timeout=None, as_json=False):
    """Thin hosted transport. Commands are never automatically retried."""
    try:
        if mode() != "hosted":
            return None
        if getattr(ctx.get_parameter_source("region"), "name", None) == "COMMANDLINE":
            raise AccountError("HOSTED_OPTION_UNSUPPORTED", "Region is service-managed.")
        from agent_sandbox.models import ExecResult

        body = {"command": command[0], "args": command[1:], "cwd": cwd, "env": env}
        if timeout is not None:
            body["timeout"] = timeout
        try:
            result = AccountClient(service_url()).authenticated(
                "POST", "/v1/vms/" + quote(name, safe="") + "/exec", body=body, timeout=None
            )
        except AccountError as exc:
            if exc.code == "SERVICE_UNAVAILABLE":
                raise AccountError(
                    "EXEC_OUTCOME_UNKNOWN",
                    "Connection lost; command may have run. Do not retry blindly.",
                ) from None
            raise
        return ExecResult(
            result["exit_code"],
            base64.b64decode(result["stdout"]),
            base64.b64decode(result["stderr"]),
        )
    except AccountError as exc:
        fail(exc, as_json)


def route(ctx, action, *, name=None, request_key=None, as_json=False):
    """Return True when handled remotely; never read local AWS sandbox state."""
    try:
        if mode() != "hosted":
            return False
        try:
            wait_seconds = int(os.environ.get("VMNEST_WAIT_SECONDS", "30"))
            if wait_seconds < 0:
                raise ValueError()
        except ValueError:
            raise AccountError(
                "INVALID_CONFIG", "VMNEST_WAIT_SECONDS must be nonnegative."
            ) from None
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
            deadline = time.monotonic() + wait_seconds
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
