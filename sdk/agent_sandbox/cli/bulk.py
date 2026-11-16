"""Bounded computer batches with independent outcomes and stable output order."""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

import typer

from agent_sandbox.accounts import AccountError
from agent_sandbox.errors import SandboxError
from agent_sandbox.remote import vm_path


def create_many(names, body, parallel, as_json, request):
    """Derive retry identities from batch key and name, independently of ordering."""
    if any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,62}", name) for name in names):
        raise typer.BadParameter("Names must start with a letter and use letters, digits, _ or -.")
    batch_key = body["request_key"]

    def perform(name):
        key = "batch-" + hashlib.sha256(json.dumps([batch_key, name]).encode()).hexdigest()
        item = {"name": name, "request_key": key}
        try:
            result = request("POST", "/v1/vms", {**body, "name": name, "request_key": key})
            if not isinstance(result, dict):
                raise SandboxError("Invalid backend response; operation may have completed.")
            return {**item, "ok": True, "result": {**result, "request_key": key}}
        except (SandboxError, AccountError) as exc:
            return {**item, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        items = list(pool.map(perform, names))
    failed = sum(not item["ok"] for item in items)
    summary = {
        "action": "create",
        "request_key": batch_key,
        "ok": not failed,
        "succeeded": len(items) - failed,
        "failed": failed,
        "items": items,
    }
    if as_json:
        typer.echo(json.dumps(summary))
    else:
        typer.echo(f"Batch request key: {batch_key}")
        for item in items:
            if item["ok"]:
                typer.echo(f"{item['name']}: creation accepted; vmnest wait {item['name']}")
            else:
                typer.echo(f"{item['name']}: {item['error']}")
                typer.echo(f"  Retry this computer with --request-key {item['request_key']}")
        typer.echo(f"{len(items) - failed} accepted; {failed} failed.")
    if failed:
        raise typer.Exit(1)


def lifecycle(action, names, all_computers, parallel, dry_run, as_json, request, present):
    if bool(names) == all_computers:
        raise typer.BadParameter("Provide computer names or --all, but not both.")
    try:
        selected = (
            [item["name"] for item in request("GET", "/v1/vms")["items"]]
            if all_computers
            else names
        )
    except (SandboxError, AccountError) as exc:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": str(exc), "items": []}))
        else:
            typer.echo(f"Could not list computers: {exc}", err=True)
        raise typer.Exit(1) from None
    selected = list(dict.fromkeys(selected))
    if dry_run:
        result = {"action": action, "dry_run": True, "names": selected}
        typer.echo(
            json.dumps(result)
            if as_json
            else f"Would {action}: " + (", ".join(selected) or "no computers")
        )
        return

    def perform(name):
        path = vm_path(name)
        method = "DELETE" if action == "delete" else "POST"
        endpoint = "open" if action == "wake" else action
        path += "?permanent=true" if action == "delete" else "/" + endpoint
        try:
            result = request(method, path)
            if not isinstance(result, dict):
                raise SandboxError("Invalid backend response; operation may have completed.")
            return {"name": name, "ok": True, "result": result}
        except (SandboxError, AccountError) as exc:
            return {"name": name, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        items = list(pool.map(perform, selected))
    failed = sum(not item["ok"] for item in items)
    # Preserve the existing single-computer success response shape.
    if len(selected) == 1 and not all_computers and not failed:
        result = items[0]["result"]
        if action == "delete" and not as_json:
            typer.echo(f"{selected[0]}: {result['status']}")
        else:
            present(result, as_json)
        return
    summary = {
        "action": action,
        "ok": not failed,
        "succeeded": len(items) - failed,
        "failed": failed,
        "items": items,
    }
    if as_json:
        typer.echo(json.dumps(summary))
    else:
        for item in items:
            result = item.get("result", {})
            status = result.get("status", result.get("state", "accepted"))
            typer.echo(f"{item['name']}: " + (status if item["ok"] else item["error"]))
        typer.echo(f"{len(items) - failed} succeeded; {failed} failed.")
    if failed:
        raise typer.Exit(1)
