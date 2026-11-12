"""Remote CLI transport; no AWS operations or VM policy in this module."""

import base64
import functools
import uuid
from pathlib import Path

from agent_sandbox.errors import SandboxError
from agent_sandbox.models import ExecResult
from agent_sandbox.remote import remote_url, request, vm_path


def routed(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if not remote_url():
            return function(*args, **kwargs)
        from .main import _fail, _print_exec, console

        operation = function.__name__
        key = kwargs.get("request_key") or str(uuid.uuid4())
        try:
            for option in ("role", "region", "egress_connector", "egress", "log_group"):
                if kwargs.get(option):
                    raise SandboxError(f"--{option.replace('_', '-')} is backend-managed")
            if kwargs.get("image") not in (None, "default"):
                raise SandboxError("Remote mode uses the backend image; use 'default' with run")
            if kwargs.get("cpus", 1) != 1 or kwargs.get("memory", 512) != 512:
                raise SandboxError("Machine size is configured by the backend image")
            name = kwargs.get("name")
            path = vm_path(name) if name else "/v1/vms"
            if operation == "create":
                body = {"name": name, "request_key": key}
                if kwargs.get("agent"):
                    if kwargs["agent"] not in {"codex", "claude"}:
                        raise SandboxError("--agent must be codex or claude")
                    body["agent"] = kwargs["agent"]
                result = request("POST", "/v1/vms", body)
                if not result.get("id"):
                    raise SandboxError(f"Creation is {result['state']}; inspect before proceeding")
                console.print(f"created {name} ({result['id']})")
                return
            if operation in {"exec", "run"}:
                cmd = kwargs.get("cmd")
                if not cmd:
                    raise SandboxError("Provide a command after --")
                endpoint = "/v1/run" if operation == "run" else path + "/exec"
                result = request(
                    "POST", endpoint, {"command": cmd[0], "args": cmd[1:], "request_key": key}
                )
                _print_exec(
                    ExecResult(
                        result["exit_code"],
                        base64.b64decode(result["stdout"]),
                        base64.b64decode(result["stderr"]),
                    )
                )
            elif operation in {"start", "stop", "rm"}:
                if operation == "rm":
                    result = request("DELETE", path)
                else:
                    result = request(
                        "POST", path + ("/resume" if operation == "start" else "/suspend")
                    )
                console.print(f"{name}: {result['state']}")
            elif operation in {"inspect", "ps"}:
                result = request("GET", path)
                if operation == "inspect":
                    console.print_json(data=result)
                else:
                    console.print(f"{name}: {result['state']} ({result['id']})")
            elif operation == "ls":
                from rich.table import Table

                table = Table("NAME", "MICROVM ID", "STATUS", "ENDPOINT")
                for row in request("GET", path)["items"]:
                    table.add_row(
                        row["name"], row["id"] or "-", row["state"], row.get("endpoint") or "-"
                    )
                console.print(table)
            elif operation == "logs":
                for event in request("GET", path + "/logs"):
                    console.out(event.get("message", ""), end="")
            elif operation == "metrics":
                from rich.table import Table

                table = Table("METRIC", "LATEST")
                for key, value in request("GET", path + "/metrics").items():
                    table.add_row(key, f"{value:.1f}" if value is not None else "-")
                console.print(table)
            else:
                raise SandboxError("This operation is not available through the remote API")
        except SandboxError as exc:
            if operation in {"create", "run", "exec"}:
                _fail(
                    f"{exc}\nRequest key: {key}. "
                    f"Reuse --request-key {key} to check the same operation."
                )
            _fail(str(exc))

    return wrapper


def register_files(app):
    @app.command()
    def upload(name: str, source: Path, destination: str):
        """Copy a local file into a VM (direct or remote)."""
        transfer(name, source, destination, True)

    @app.command()
    def download(name: str, source: str, destination: Path):
        """Copy a VM file to a local path (direct or remote)."""
        transfer(name, destination, source, False)


def transfer(name, local, guest, upload):
    from .main import Sandbox, _fail, _require_record, _run

    try:
        if remote_url():
            path = vm_path(name) + "/files/"
            if upload:
                request(
                    "POST",
                    path + "write",
                    {"path": guest, "content": base64.b64encode(local.read_bytes()).decode()},
                )
            else:
                result = request("POST", path + "read", {"path": guest})
                local.write_bytes(base64.b64decode(result["content"]))
        else:

            async def perform():
                rec = _require_record(name)
                vm = await Sandbox.attach(name, rec.microvm_id, region=rec.region)
                try:
                    if upload:
                        await vm.write_file(guest, local.read_bytes())
                    else:
                        local.write_bytes(await vm.read_file(guest))
                finally:
                    if vm._agent:
                        await vm._agent.aclose()

            _run(perform())
    except (SandboxError, OSError) as exc:
        _fail(str(exc))
