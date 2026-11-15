"""Bounded computer batches with independent outcomes and stable output order."""

import json
from concurrent.futures import ThreadPoolExecutor

import typer

from agent_sandbox.accounts import AccountError
from agent_sandbox.errors import SandboxError
from agent_sandbox.remote import vm_path


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
        path += "?permanent=true" if action == "delete" else "/" + action
        try:
            return {"name": name, "ok": True, "result": request(method, path)}
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
