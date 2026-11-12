"""Remote CLI transport; no AWS operations or VM policy in this module."""

import base64
import functools
import uuid
from pathlib import Path

from agent_sandbox.accounts import AccountError
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
                from .agent_runs import prompt_text, submit

                text = None
                if kwargs.get("prompt") is not None or kwargs.get("prompt_file") is not None:
                    if not kwargs.get("agent"):
                        raise SandboxError("--prompt requires --agent")
                    text = prompt_text(kwargs.get("prompt"), kwargs.get("prompt_file"))
                body = {"name": name, "request_key": key}
                for field in ("repository", "setup_command", "idle_minutes"):
                    if kwargs.get(field) is not None:
                        body[field] = kwargs[field]
                if kwargs.get("approvals"):
                    if not kwargs.get("agent"):
                        raise SandboxError("--approvals requires --agent")
                    body["approvals"] = True
                if kwargs.get("disposable"):
                    body["disposable"] = True
                if kwargs.get("agent"):
                    if kwargs["agent"] not in {"codex", "claude"}:
                        raise SandboxError("--agent must be codex or claude")
                    body["agent"] = kwargs["agent"]
                if kwargs.get("profile"):
                    if kwargs.get("agent") not in {"codex", "claude"}:
                        raise SandboxError("--profile requires --agent codex or claude")
                    body["profile"] = kwargs["profile"]
                result = request("POST", "/v1/vms", body)
                if not result.get("id"):
                    raise SandboxError(f"Creation is {result['state']}; inspect before proceeding")
                if text is not None:
                    import time

                    from .agent_runs import poll_interval

                    while result.get("project") and result["project"]["setup_status"] in (
                        "pending",
                        "running",
                    ):
                        if result["state"] != "RUNNING":
                            raise SandboxError(
                                "VM stopped before project setup completed; "
                                "inspect it before retrying"
                            )
                        time.sleep(poll_interval())
                        result = request("GET", path)
                    if result.get("project") and result["project"]["setup_status"] != "ready":
                        raise SandboxError(
                            "VM was created but project setup needs attention; "
                            "inspect it and use vmnest project logs"
                        )
                    try:
                        result["run"] = submit(
                            name,
                            text,
                            request_key="create-"
                            + __import__("hashlib").sha256(key.encode()).hexdigest(),
                        )
                    except Exception as exc:
                        raise SandboxError(
                            f"VM {name} was created, but prompt submission failed: {exc}"
                        ) from None
                result["request_key"] = key
                if kwargs.get("as_json"):
                    console.print_json(data=result)
                else:
                    console.print(f"Created {name}. Use vmnest open {name} or vmnest exec {name} -- COMMAND.", markup=False)
                    if result.get("run"):
                        console.print(f"Run {result['run']['id']}: {result['run']['status']}")
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
                    from .computers import present

                    present(result, kwargs.get("as_json", False))
                else:
                    console.print(f"{name}: {result.get('status', result['state'])}")
            elif operation == "ls":
                from rich.table import Table

                result = request("GET", path)
                if kwargs.get("as_json"):
                    console.print_json(data=result)
                    return
                table = Table("COMPUTER", "TYPE", "STATUS")
                for row in result["items"]:
                    table.add_row(
                        row["name"], (row.get("agent") or {}).get("provider", "Computer"),
                        row.get("status", row["state"]).replace("_", " ")
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
        except (SandboxError, AccountError) as exc:
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
