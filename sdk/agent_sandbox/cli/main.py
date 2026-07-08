"""``asb`` CLI: an msb-style interface over the agent_sandbox SDK.

Every command is a thin wrapper over :class:`agent_sandbox.Sandbox` and the
control plane. Named sandboxes are tracked in a local :class:`StateStore`.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets

import typer
from rich.console import Console
from rich.table import Table

from agent_sandbox.control_plane import ControlPlane
from agent_sandbox.errors import SandboxError
from agent_sandbox.sandbox import ENV_IMAGE_ARN, ENV_ROLE_ARN, Sandbox

from .state import SandboxRecord, StateStore

app = typer.Typer(
    name="asb",
    help="Agent Sandbox: isolated microVM sandboxes on AWS Lambda MicroVMs.",
    no_args_is_help=True,
    add_completion=False,
)
image_app = typer.Typer(name="image", help="Manage MicroVM images.", no_args_is_help=True)
app.add_typer(image_app)
infra_app = typer.Typer(
    name="infra",
    help="Deploy/manage MicroVM infrastructure from sandbox.yaml.",
    no_args_is_help=True,
)
app.add_typer(infra_app)

console = Console()
err_console = Console(stderr=True)


def _run(coro):
    return asyncio.run(coro)


def _fail(message: str) -> None:
    err_console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=1)


_infra_outputs_cache: dict | None = None


def _infra_outputs() -> dict:
    """Best-effort infra stack outputs for auto-wiring create/run.

    Reads sandbox.yaml (if present) and the selected stack's outputs. Never raises;
    returns {} when infra isn't set up so callers fall back to env/flags.
    """
    global _infra_outputs_cache
    if _infra_outputs_cache is not None:
        return _infra_outputs_cache
    outputs: dict = {}
    try:
        from agent_sandbox.infra.config import load_setup
        from agent_sandbox.infra.provisioner import read_outputs

        outputs = read_outputs(load_setup())
    except Exception:  # noqa: BLE001 - auto-wire is optional
        outputs = {}
    _infra_outputs_cache = outputs
    return outputs


def _resolve_image(image: str | None) -> str:
    resolved = image or os.environ.get(ENV_IMAGE_ARN) or _infra_outputs().get("image_arn")
    if not resolved:
        _fail(f"--image is required (or set ${ENV_IMAGE_ARN}, or run `asb infra up`).")
    return resolved  # type: ignore[return-value]


def _resolve_role(role: str | None) -> str:
    resolved = (
        role or os.environ.get(ENV_ROLE_ARN) or _infra_outputs().get("execution_role_arn")
    )
    if not resolved:
        _fail(f"--role is required (or set ${ENV_ROLE_ARN}, or run `asb infra up`).")
    return resolved  # type: ignore[return-value]


def _resolve_egress_connectors(
    egress_connector: str | None, use_infra: bool
) -> list[str] | None:
    """Resolve the egress network connector(s) to attach at run time.

    Precedence: an explicit ``--egress-connector`` ARN wins; otherwise ``--egress``
    pulls the provisioned connector from infra outputs. Returns ``None`` when no
    connector should be attached (the default).
    """
    if egress_connector:
        return [egress_connector]
    if use_infra:
        arn = _infra_outputs().get("egress_network_connector_arn")
        if not arn:
            _fail(
                "--egress given but no egress connector in infra outputs. "
                "Configure network.egress in sandbox.yaml and run `asb infra up`, "
                "or pass --egress-connector <arn>."
            )
        return [arn]
    return None




def _print_exec(result) -> None:
    if result.stdout:
        console.out(result.stdout_text, end="")
    if result.stderr:
        err_console.out(result.stderr_text, end="")
    if not result.success:
        raise typer.Exit(code=result.exit_code)


# -- lifecycle -------------------------------------------------------------


@app.command()
def create(
    name: str = typer.Argument(..., help="Sandbox name."),
    image: str | None = typer.Option(None, "--image", "-i", help="MicroVM image ARN."),
    cpus: int = typer.Option(1, "--cpus", help="vCPU count."),
    memory: int = typer.Option(512, "--memory", "-m", help="Memory in MB."),
    role: str | None = typer.Option(None, "--role", help="Execution role ARN."),
    region: str | None = typer.Option(None, "--region", help="AWS region."),
    egress_connector: str | None = typer.Option(
        None, "--egress-connector", help="Egress network connector ARN to attach."
    ),
    egress: bool = typer.Option(
        False, "--egress", "-e",
        help="Attach the egress connector from infra outputs.",
    ),
) -> None:
    """Create and start a named sandbox."""
    image_arn = _resolve_image(image)
    role_arn = _resolve_role(role)
    egress_connectors = _resolve_egress_connectors(egress_connector, egress)

    async def _do() -> None:
        sandbox = await Sandbox.create(
            name,
            image_arn=image_arn,
            cpus=cpus,
            memory=memory,
            execution_role_arn=role_arn,
            region=region,
            egress_network_connectors=egress_connectors,
        )
        StateStore().put(
            SandboxRecord(
                name=name,
                microvm_id=sandbox.microvm_id,
                endpoint=sandbox.endpoint,
                image_arn=image_arn,
                region=region,
            )
        )
        console.print(f"[green]created[/green] {name} ({sandbox.microvm_id})")

    _guard(_do())


@app.command()
def run(
    image: str = typer.Argument(..., help="MicroVM image ARN."),
    cmd: list[str] = typer.Argument(None, help="Command after `--`."),
    cpus: int = typer.Option(1, "--cpus"),
    memory: int = typer.Option(512, "--memory", "-m"),
    role: str | None = typer.Option(None, "--role"),
    region: str | None = typer.Option(None, "--region"),
    egress_connector: str | None = typer.Option(
        None, "--egress-connector", help="Egress network connector ARN to attach."
    ),
    egress: bool = typer.Option(
        False, "--egress", "-e",
        help="Attach the egress connector from infra outputs.",
    ),
) -> None:
    """Boot an ephemeral sandbox, run a command, then terminate it."""
    if not cmd:
        _fail("provide a command after `--`, e.g. asb run <arn> -- python -c 'print(1)'")
    role_arn = _resolve_role(role)
    egress_connectors = _resolve_egress_connectors(egress_connector, egress)
    ephemeral = f"asb-run-{secrets.token_hex(4)}"

    async def _do() -> None:
        sandbox = await Sandbox.create(
            ephemeral,
            image_arn=image,
            cpus=cpus,
            memory=memory,
            execution_role_arn=role_arn,
            region=region,
            egress_network_connectors=egress_connectors,
        )
        try:
            result = await sandbox.exec(cmd[0], cmd[1:])
        finally:
            await sandbox.terminate()
        _print_exec(result)

    _guard(_do())


@app.command()
def exec(
    name: str = typer.Argument(..., help="Sandbox name."),
    cmd: list[str] = typer.Argument(None, help="Command after `--`."),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Execute a command in a running sandbox."""
    if not cmd:
        _fail("provide a command after `--`, e.g. asb exec app -- python -c 'print(1)'")
    rec = _require_record(name)

    async def _do() -> None:
        sandbox = await Sandbox.attach(
            name, rec.microvm_id, endpoint=rec.endpoint, region=region or rec.region
        )
        result = await sandbox.exec(cmd[0], cmd[1:])
        _print_exec(result)

    _guard(_do())


@app.command()
def stop(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Suspend a sandbox (preserves memory/disk; resume with `asb start`)."""
    rec = _require_record(name)

    async def _do() -> None:
        sandbox = await Sandbox.attach(name, rec.microvm_id, region=region or rec.region)
        await sandbox.suspend()
        console.print(f"[yellow]suspended[/yellow] {name}")

    _guard(_do())


@app.command()
def start(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Resume a suspended sandbox."""
    rec = _require_record(name)

    async def _do() -> None:
        sandbox = await Sandbox.attach(name, rec.microvm_id, region=region or rec.region)
        await sandbox.start()
        StateStore().put(
            SandboxRecord(
                name=name,
                microvm_id=sandbox.microvm_id,
                endpoint=sandbox.endpoint,
                image_arn=rec.image_arn,
                region=rec.region,
            )
        )
        console.print(f"[green]resumed[/green] {name}")

    _guard(_do())


@app.command()
def rm(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Terminate a sandbox and remove it from local state."""
    rec = _require_record(name)

    async def _do() -> None:
        sandbox = await Sandbox.attach(name, rec.microvm_id, region=region or rec.region)
        await sandbox.terminate()
        StateStore().delete(name)
        console.print(f"[red]removed[/red] {name}")

    _guard(_do())


# -- status ----------------------------------------------------------------


@app.command()
def ls(region: str | None = typer.Option(None, "--region")) -> None:
    """List all locally tracked sandboxes and their live status."""
    records = StateStore().all()
    if not records:
        console.print("no sandboxes")
        return

    async def _status(rec: SandboxRecord) -> str:
        try:
            cp = ControlPlane(region=region or rec.region)
            info = await cp.get_microvm(rec.microvm_id)
            return info.status or "unknown"
        except SandboxError:
            return "unreachable"

    async def _do() -> None:
        table = Table("NAME", "MICROVM ID", "STATUS", "ENDPOINT")
        for rec in records:
            status = await _status(rec)
            table.add_row(rec.name, rec.microvm_id, status, rec.endpoint or "-")
        console.print(table)

    _guard(_do())


@app.command()
def ps(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Show a single sandbox's status."""
    rec = _require_record(name)

    async def _do() -> None:
        cp = ControlPlane(region=region or rec.region)
        info = await cp.get_microvm(rec.microvm_id)
        console.print(f"{name}: {info.status or 'unknown'} ({rec.microvm_id})")

    _guard(_do())


@app.command()
def inspect(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Print detailed MicroVM info as JSON."""
    rec = _require_record(name)

    async def _do() -> None:
        cp = ControlPlane(region=region or rec.region)
        info = await cp.get_microvm(rec.microvm_id)
        console.print_json(json.dumps(info.raw, default=str))

    _guard(_do())


@app.command()
def logs(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
    log_group: str | None = typer.Option(
        None, "--log-group", help="Override the CloudWatch log group."
    ),
) -> None:
    """Fetch CloudWatch logs for a sandbox (best-effort)."""
    rec = _require_record(name)
    _print_cw_logs(rec, region=region or rec.region, log_group=log_group)


@app.command()
def metrics(
    name: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Fetch recent CloudWatch metrics for a sandbox (best-effort)."""
    rec = _require_record(name)
    _print_cw_metrics(rec, region=region or rec.region)


# -- images ----------------------------------------------------------------


@image_app.command("build")
def image_build(
    directory: str = typer.Argument(..., help="Directory with Dockerfile + code."),
    name: str = typer.Option(..., "--name", "-n", help="Image name."),
    bucket: str = typer.Option(..., "--bucket", "-b", help="S3 bucket for the build zip."),
    role: str | None = typer.Option(None, "--role"),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Zip a directory, upload to S3, and create a MicroVM image."""
    import boto3

    from agent_sandbox.infra import resources as R

    src = os.path.abspath(directory)
    if not os.path.isdir(src):
        _fail(f"not a directory: {directory}")

    key = f"microvm-images/{name}.zip"
    s3 = boto3.client("s3", region_name=region)
    R.upload_guest(s3, bucket=bucket, key=key, guest_dir=src)

    async def _do() -> None:
        cp = ControlPlane(region=region)
        resp = await cp.create_microvm_image(
            name=name, s3_bucket=bucket, s3_key=key, execution_role_arn=role
        )
        console.print_json(json.dumps(resp, default=str))

    _guard(_do())


@image_app.command("ls")
def image_ls(
    region: str | None = typer.Option(None, "--region"),
    managed: bool = typer.Option(
        False, "--managed", help="List AWS-provided base images instead of your own."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """List MicroVM images (your own by default; --managed for base images)."""

    async def _do() -> None:
        cp = ControlPlane(region=region)
        images = (
            await cp.list_managed_microvm_images()
            if managed
            else await cp.list_microvm_images()
        )
        if not images:
            console.print("no images")
            return
        if json_output:
            console.print_json(json.dumps(images, default=str))
            return
        table = Table(show_header=True, header_style="bold")
        table.add_column("name")
        table.add_column("arn", overflow="fold")
        table.add_column("state")
        table.add_column("active version")
        table.add_column("created")
        for im in images:
            table.add_row(
                str(im.get("name") or "-"),
                str(im.get("imageArn") or "-"),
                str(im.get("state") or "-"),
                str(im.get("latestActiveImageVersion") or "-"),
                str(im.get("createdAt") or "-"),
            )
        console.print(table)

    _guard(_do())


@image_app.command("rm")
def image_rm(
    image_arn: str = typer.Argument(...),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """Delete a MicroVM image."""

    async def _do() -> None:
        cp = ControlPlane(region=region)
        await cp.delete_microvm_image(image_arn)
        console.print(f"[red]deleted[/red] {image_arn}")

    _guard(_do())


# -- helpers ---------------------------------------------------------------


# `asb forward` polls the control plane on this cadence so it can disconnect
# itself once the MicroVM is no longer RUNNING (e.g. after `asb stop`/`asb rm`).
_FORWARD_POLL_INTERVAL = 5.0
_FORWARD_TOKEN_REFRESH = 25 * 60
_FORWARD_MAX_STATUS_FAILURES = 3

# Statuses that mean the VM is gone/going away; anything else (RUNNING, empty,
# or a transient state like PENDING/STARTING) keeps the tunnel serving.
_NOT_RUNNING_STATUSES = frozenset(
    {"SUSPENDED", "SUSPENDING", "STOPPING", "STOPPED", "TERMINATING", "TERMINATED", "FAILED"}
)


def _vm_is_gone(status: str | None) -> bool:
    """True if ``status`` indicates the MicroVM is suspended/terminated/failed."""
    if not status:
        return False
    return status.strip().upper() in _NOT_RUNNING_STATUSES


def _is_websocket_upgrade(headers) -> bool:
    """True if ``headers`` carry a WebSocket upgrade handshake (RFC 6455): a
    ``Connection`` header with an ``upgrade`` token, plus ``Upgrade: websocket``.
    """
    connection_tokens = {
        tok.strip().lower() for tok in headers.get("Connection", "").split(",") if tok.strip()
    }
    upgrade = headers.get("Upgrade", "").strip().lower()
    return "upgrade" in connection_tokens and upgrade == "websocket"


def _merge_websocket_protocols(existing: str | None, *extra: str) -> str:
    """Append ``extra`` subprotocols to a raw ``Sec-WebSocket-Protocol`` value.

    Preserves the client's requested order and skips anything already present,
    so e.g. an app-level subprotocol like ``vite-hmr`` is kept alongside the
    lambda-microvms auth/port subprotocols appended for the upstream leg.
    """
    tokens = [tok.strip() for tok in (existing or "").split(",") if tok.strip()]
    for protocol in extra:
        if protocol not in tokens:
            tokens.append(protocol)
    return ", ".join(tokens)


def _parse_upstream_addr(base_url: str) -> tuple[str | None, int, bool]:
    """Split a sandbox base URL into ``(host, port, is_tls)`` for a raw socket connect."""
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    is_tls = parsed.scheme == "https"
    port = parsed.port or (443 if is_tls else 80)
    return parsed.hostname, port, is_tls


def _relay_bidirectional(a, b, *, bufsize: int = 65536) -> None:
    """Pump bytes between two connected sockets until either side closes.

    Runs ``b`` -> ``a`` on a helper thread and ``a`` -> ``b`` inline, so the
    caller's thread blocks until the connection ends in either direction.
    """
    import socket
    import threading

    def pump(src, dst) -> None:
        try:
            while True:
                chunk = src.recv(bufsize)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    t = threading.Thread(target=pump, args=(b, a), daemon=True)
    t.start()
    pump(a, b)
    t.join(timeout=5)


def _websocket_handshake_and_relay(
    handler,
    *,
    base_url: str,
    verify: bool,
    auth_token: str,
    remote_port: int,
) -> None:
    """Relay a WebSocket upgrade (and subsequent frames) from ``handler`` to the
    sandbox's endpoint.

    ``httpx`` has no upgrade/duplex-socket support, so this bypasses it and
    speaks raw HTTP/1.1 on a socket. The lambda-microvms auth token and target
    port are injected as ``Sec-WebSocket-Protocol`` subprotocols (rather than
    the ``X-aws-proxy-*`` headers plain HTTP forwarding uses), matching the
    proxy's documented WebSocket convention -- see ``references/networking.md``
    in the aws-lambda-microvms skill. ``handler`` only needs ``command``,
    ``path``, ``headers``, ``connection``, and ``send_error`` (the subset of
    ``BaseHTTPRequestHandler`` this uses), so tests can pass a minimal stand-in.
    """
    import socket
    import ssl

    host, port, is_tls = _parse_upstream_addr(base_url)
    try:
        raw = socket.create_connection((host, port), timeout=10)
    except OSError as exc:
        handler.send_error(502, f"upstream connection failed: {exc}")
        return

    if is_tls:
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        upstream = ctx.wrap_socket(raw, server_hostname=host)
    else:
        upstream = raw

    protocols = _merge_websocket_protocols(
        handler.headers.get("Sec-WebSocket-Protocol"),
        "lambda-microvms",
        f"lambda-microvms.authentication.{auth_token}",
        f"lambda-microvms.port.{remote_port}",
    )
    lines = [f"{handler.command} {handler.path} HTTP/1.1\r\n"]
    for key, val in handler.headers.items():
        low = key.lower()
        if low == "host":
            lines.append(f"Host: {host}\r\n")
        elif low == "sec-websocket-protocol":
            continue
        else:
            lines.append(f"{key}: {val}\r\n")
    lines.append(f"Sec-WebSocket-Protocol: {protocols}\r\n")
    lines.append("\r\n")

    try:
        upstream.sendall("".join(lines).encode("latin-1"))
    except OSError as exc:
        handler.send_error(502, f"upstream error: {exc}")
        upstream.close()
        return

    upstream_reader = upstream.makefile("rb")
    status_line = upstream_reader.readline()
    if not status_line:
        handler.send_error(502, "upstream closed during handshake")
        upstream.close()
        return
    header_bytes = [status_line]
    while True:
        line = upstream_reader.readline()
        header_bytes.append(line)
        if line in (b"\r\n", b"\n", b""):
            break

    try:
        handler.connection.sendall(b"".join(header_bytes))
    except OSError:
        upstream.close()
        return

    status_code = int(status_line.split(b" ", 2)[1])
    if status_code != 101:
        # Handshake rejected upstream; the real status/headers were already
        # forwarded verbatim above, so there's nothing left to relay.
        upstream.close()
        return

    # `upstream_reader`'s internal buffer may already hold bytes past the
    # header boundary (e.g. the first WS frame arriving in the same TCP
    # segment as the 101 response) -- those are invisible to the raw
    # `upstream.recv()` calls in `_relay_bidirectional`, so flush them first.
    pending = upstream_reader.peek()
    if pending:
        upstream_reader.read(len(pending))
        try:
            handler.connection.sendall(pending)
        except OSError:
            upstream.close()
            return

    try:
        _relay_bidirectional(handler.connection, upstream)
    finally:
        upstream.close()


def _require_record(name: str) -> SandboxRecord:
    try:
        return StateStore().require(name)
    except KeyError as exc:
        _fail(str(exc))
        raise  # unreachable, keeps type checkers happy


def _guard(coro) -> None:
    try:
        _run(coro)
    except SandboxError as exc:
        _fail(str(exc))


def _print_cw_logs(rec: SandboxRecord, region: str | None, log_group: str | None) -> None:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    group = log_group or f"/aws/lambda-microvms/{rec.microvm_id}"
    client = boto3.client("logs", region_name=region)
    try:
        streams = client.describe_log_streams(
            logGroupName=group, orderBy="LastEventTime", descending=True, limit=1
        ).get("logStreams", [])
        if not streams:
            console.print(f"no log streams in {group}")
            return
        events = client.get_log_events(
            logGroupName=group, logStreamName=streams[0]["logStreamName"]
        ).get("events", [])
        for event in events:
            console.out(event.get("message", ""), end="")
    except (ClientError, BotoCoreError) as exc:
        _fail(f"could not read logs from {group}: {exc}")


def _print_cw_metrics(rec: SandboxRecord, region: str | None) -> None:
    import datetime as dt

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("cloudwatch", region_name=region)
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(minutes=30)
    table = Table("METRIC", "LATEST")
    try:
        for metric in ("CPUUtilization", "MemoryUtilization"):
            resp = client.get_metric_statistics(
                Namespace="AWS/LambdaMicroVMs",
                MetricName=metric,
                Dimensions=[{"Name": "MicrovmId", "Value": rec.microvm_id}],
                StartTime=start,
                EndTime=end,
                Period=60,
                Statistics=["Average"],
            )
            points = sorted(resp.get("Datapoints", []), key=lambda p: p["Timestamp"])
            latest = f"{points[-1]['Average']:.1f}" if points else "-"
            table.add_row(metric, latest)
        console.print(table)
    except (ClientError, BotoCoreError) as exc:
        _fail(f"could not read metrics: {exc}")


# -- infra -----------------------------------------------------------------

SETUP_TEMPLATE = """\
# agent-sandbox-os infrastructure config (sandbox.yaml).
# Each resource is reuse-or-create: set an existing id/arn to reuse it, or leave
# it empty to have `asb infra up` create it.

project: agent-sandbox-os
stack: dev
region: us-east-1

image:
  name: agent-sandbox-guest
  guest_dir: ./guest            # zipped -> S3 -> create_microvm_image
  base_image_arn: ""            # empty -> newest managed base image (e.g. al2023)
  base_image_version: ""        # optional

role:
  arn: ""                       # set -> reuse; empty -> create
  name: agent-sandbox-exec
  extra_policy_arns: []         # optional managed policy ARNs to attach

bucket:
  name: ""                      # set -> reuse; empty -> create

network:                        # OPTIONAL. Omit entirely for default public egress.
  egress:                       # VPC egress network connector (reuse-or-create).
    connector_arn: ""           # set -> reuse; empty -> create a VPC_EGRESS connector
    name: agent-sandbox-egress  # name for the created connector (+ SG)
    vpc_id: ""                  # empty -> default VPC
    subnet_ids: []              # subnet ids or Name tags; empty -> discover in VPC
    security_group_id: ""       # set -> reuse; empty -> create egress-only SG
    operator_role_arn: ""       # set -> reuse; empty -> create NetworkConnectorOperatorRole
  # Note: VPC_EGRESS replaces public egress; add a NAT gateway for internet + VPC.
"""


def _load_infra_config(setup_file: str | None, stack: str | None):
    try:
        from agent_sandbox.infra.config import SetupError, load_setup
    except ImportError:
        _fail(r"infra extra not installed. Run `pip install agent-sandbox-os\[infra]`.")
    try:
        cfg = load_setup(setup_file)
    except SetupError as exc:
        _fail(str(exc))
    if stack:
        cfg.stack = stack
    return cfg


def _provisioner(setup_file: str | None, stack: str | None):
    cfg = _load_infra_config(setup_file, stack)
    from agent_sandbox.infra.provisioner import Provisioner

    return Provisioner(cfg)


@infra_app.command("init")
def infra_init(
    path: str = typer.Option(
        "sandbox.yaml", "--file", "-f", help="Where to write the config."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite if it exists."),
) -> None:
    """Scaffold a commented sandbox.yaml."""
    if os.path.exists(path) and not force:
        _fail(f"{path} already exists (use --force to overwrite).")
    with open(path, "w") as fh:
        fh.write(SETUP_TEMPLATE)
    console.print(f"[green]wrote[/green] {path}")


@infra_app.command("preview")
def infra_preview(
    setup_file: str | None = typer.Option(None, "--file", "-f"),
    stack: str | None = typer.Option(None, "--stack", "-s"),
) -> None:
    """Preview infrastructure changes (create/reuse per resource)."""
    actions = _provisioner(setup_file, stack).plan()
    table = Table("RESOURCE", "NAME", "ACTION")
    for a in actions:
        table.add_row(a["resource"], a["name"], a["action"])
    console.print(table)


@infra_app.command("up")
def infra_up(
    files: list[str] = typer.Option(
        [], "--file", "-f",
        help="Config file(s) or directories; repeatable (kubectl-style).",
    ),
    stack: str | None = typer.Option(None, "--stack", "-s"),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a new MicroVM image version even if one is active."
    ),
    parallelism: int = typer.Option(
        0, "--parallelism", "-p",
        help="Max projects to provision concurrently (0 = one per project).",
    ),
) -> None:
    """Create or update infrastructure for one or more projects, then print outputs."""
    from agent_sandbox.infra.config import SetupError
    from agent_sandbox.infra.provisioner import Provisioner

    try:
        from agent_sandbox.infra.config import load_setups
    except ImportError:
        _fail(r"infra extra not installed. Run `pip install agent-sandbox-os\[infra]`.")

    try:
        configs = load_setups(files, stack=stack)
    except SetupError as exc:
        _fail(str(exc))

    if len(configs) == 1:
        cfg = configs[0]

        def _report(msg: str) -> None:
            console.print(f"[cyan]→[/cyan] {msg}")

        try:
            outputs = Provisioner(cfg).up(rebuild=rebuild, report=_report)
        except Exception as exc:  # noqa: BLE001 - surface as a clean CLI error
            _fail(f"{cfg.project}/{cfg.stack}: {exc}")
        console.print("[green]up complete[/green]")
        console.print_json(json.dumps(outputs, default=str))
        return

    _run_parallel_up(configs, rebuild=rebuild, parallelism=parallelism)


def _run_parallel_up(configs: list, *, rebuild: bool, parallelism: int) -> None:
    """Provision multiple projects concurrently over a shared thread-safe store."""
    import concurrent.futures

    from agent_sandbox.infra.provisioner import Provisioner
    from agent_sandbox.infra.state import InfraStateStore

    store = InfraStateStore()  # one shared, lock-guarded store for all projects

    def _run_one(cfg) -> dict:
        label = f"{cfg.project}/{cfg.stack}"

        def _report(msg: str) -> None:
            console.print(f"[cyan]→[/cyan] [dim]{label}[/dim] {msg}")

        return Provisioner(cfg, store=store).up(rebuild=rebuild, report=_report)

    workers = parallelism if parallelism and parallelism > 0 else len(configs)
    results: list[tuple[str, dict | None, Exception | None]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, cfg): f"{cfg.project}/{cfg.stack}" for cfg in configs
        }
        for future in concurrent.futures.as_completed(futures):
            label = futures[future]
            try:
                results.append((label, future.result(), None))
            except Exception as exc:  # noqa: BLE001 - one failure must not abort others
                results.append((label, None, exc))

    results.sort(key=lambda r: r[0])
    table = Table("PROJECT/STACK", "STATUS", "DETAIL")
    failed = 0
    for label, outputs, error in results:
        if error is None:
            detail = str((outputs or {}).get("image_arn", ""))
            table.add_row(label, "[green]ok[/green]", detail)
        else:
            failed += 1
            table.add_row(label, "[red]failed[/red]", str(error))
    console.print(table)
    if failed:
        _fail(f"{failed} of {len(results)} project(s) failed")
    console.print("[green]up complete[/green]")


@infra_app.command("refresh")
def infra_refresh(
    setup_file: str | None = typer.Option(None, "--file", "-f"),
    stack: str | None = typer.Option(None, "--stack", "-s"),
) -> None:
    """Refresh state from the cloud provider."""
    _provisioner(setup_file, stack).refresh()
    console.print("[green]refreshed[/green]")


@infra_app.command("destroy")
def infra_destroy(
    setup_file: str | None = typer.Option(None, "--file", "-f"),
    stack: str | None = typer.Option(None, "--stack", "-s"),
) -> None:
    """Destroy all managed infrastructure."""
    def _report(msg: str) -> None:
        console.print(f"[cyan]→[/cyan] {msg}")

    _provisioner(setup_file, stack).destroy(report=_report)
    console.print("[red]destroyed[/red]")


@app.command()
def forward(
    name: str = typer.Argument(..., help="Sandbox name."),
    remote_port: int = typer.Option(..., "--remote-port", "-r", help="Port inside the VM."),
    local_port: int | None = typer.Option(
        None, "--local-port", "-l", help="Local port (defaults to --remote-port)."
    ),
    region: str | None = typer.Option(None, "--region"),
    no_verify_tls: bool = typer.Option(
        False, "--no-verify-tls", help="Skip TLS verification (debugging only)."
    ),
    poll_interval: float = typer.Option(
        _FORWARD_POLL_INTERVAL,
        "--poll-interval",
        help="Seconds between VM status checks (auto-disconnect when it stops).",
    ),
) -> None:
    """Port-forward a VM port to localhost so you can open it in a browser.

    Runs a local reverse proxy that injects the required auth headers, so a
    plain browser/curl request to http://localhost:<local-port> reaches the app
    listening on <remote-port> inside the sandbox. Press Ctrl+C to stop.

    WebSocket upgrades (e.g. dev-server HMR, chat apps) are also relayed: the
    auth token and target port travel as lambda-microvms subprotocols on that
    leg instead of headers.
    """
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx

    rec = _require_record(name)
    local = local_port or remote_port
    verify = not no_verify_tls
    hop_by_hop = {"content-encoding", "content-length", "transfer-encoding",
                  "connection", "keep-alive"}
    token = {"value": ""}

    async def _mint(cp: ControlPlane) -> str:
        return await cp.create_auth_token(
            rec.microvm_id, expiration_in_minutes=30, allowed_ports=[{"port": remote_port}]
        )

    async def _setup() -> tuple[str, ControlPlane]:
        sandbox = await Sandbox.attach(
            name, rec.microvm_id, endpoint=rec.endpoint,
            region=region or rec.region, verify_tls=verify,
        )
        if not sandbox.endpoint:
            _fail(f"sandbox {name!r} has no endpoint (is it running?).")
        cp = ControlPlane(region=region or rec.region)
        token["value"] = await _mint(cp)
        endpoint = sandbox.endpoint.strip()
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        return endpoint.rstrip("/"), cp

    base_url, cp = _run(_setup())
    client = httpx.Client(base_url=base_url, verify=verify, timeout=60.0)

    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            pass

        def _proxy(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            fwd = {k: v for k, v in self.headers.items()
                   if k.lower() not in {"host", "content-length", "connection"}}
            fwd["X-aws-proxy-auth"] = token["value"]
            fwd["X-aws-proxy-port"] = str(remote_port)
            try:
                up = client.request(self.command, self.path, content=body, headers=fwd)
            except httpx.HTTPError as exc:
                self.send_error(502, f"upstream error: {exc}")
                return
            self.send_response(up.status_code)
            for key, val in up.headers.items():
                if key.lower() not in hop_by_hop:
                    self.send_header(key, val)
            content = up.content
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionError):
                # Client disconnected mid-response (e.g. browser navigated away).
                return

        def _handle_get(self) -> None:
            if _is_websocket_upgrade(self.headers):
                _websocket_handshake_and_relay(
                    self,
                    base_url=base_url,
                    verify=verify,
                    auth_token=token["value"],
                    remote_port=remote_port,
                )
            else:
                self._proxy()

        do_GET = _handle_get
        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _proxy

    class _QuietThreadingHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request: object, client_address: object) -> None:
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                return  # benign: client dropped the connection
            err_console.print(f"[yellow]proxy warning:[/yellow] {exc}")

    server = _QuietThreadingHTTPServer(("127.0.0.1", local), ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    console.print(
        f"[green]forwarding[/green] http://localhost:{local} -> "
        f"{name}:{remote_port}  (Ctrl+C to stop)"
    )
    import time

    interval = poll_interval if poll_interval > 0 else _FORWARD_POLL_INTERVAL
    last_refresh = time.monotonic()
    status_failures = 0
    try:
        while True:
            time.sleep(interval)
            # Auto-disconnect once the MicroVM is no longer running (e.g. the
            # sandbox was suspended by `asb stop` or terminated by `asb rm` in
            # another terminal).
            try:
                info = _run(cp.get_microvm(rec.microvm_id))
                status_failures = 0
                if _vm_is_gone(info.status):
                    console.print(
                        f"[yellow]{name} is no longer running "
                        f"({info.status}); disconnecting[/yellow]"
                    )
                    break
            except SandboxError:
                # A single blip shouldn't drop a healthy tunnel, but repeated
                # failures usually mean the VM is gone (get_microvm raises after
                # `asb rm`), so give up after a few consecutive errors.
                status_failures += 1
                if status_failures >= _FORWARD_MAX_STATUS_FAILURES:
                    console.print(
                        f"[yellow]{name} is unreachable; disconnecting[/yellow]"
                    )
                    break
            # Refresh the auth token roughly every 25 minutes.
            if time.monotonic() - last_refresh >= _FORWARD_TOKEN_REFRESH:
                token["value"] = _run(_mint(cp))
                last_refresh = time.monotonic()
                console.print("[cyan]token refreshed[/cyan]")
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        client.close()
        console.print("stopped")


@app.command()
def mcp() -> None:
    """Run the agent-sandbox MCP server (FastMCP) over stdio.

    Exposes sandbox lifecycle, exec, filesystem, logs, metrics, and image tools
    to any MCP client. Register with Claude Code via:

        claude mcp add agent-sandbox -- asb mcp
    """
    try:
        from agent_sandbox_mcp.server import main as _mcp_main
    except ImportError:
        _fail(
            "MCP server not available. Install the `mcp` extra with "
            r"`pip install agent-sandbox-os\[mcp]` (or `uv pip install -e '.\[mcp]'` "
            "in a source checkout)."
        )
        return
    _mcp_main()


@infra_app.command("output")
def infra_output(
    name: str | None = typer.Argument(None, help="Single output name, or all if omitted."),
    setup_file: str | None = typer.Option(None, "--file", "-f"),
    stack: str | None = typer.Option(None, "--stack", "-s"),
) -> None:
    """Print stack outputs (image_arn, execution_role_arn, ...)."""
    outputs = _provisioner(setup_file, stack).outputs()
    if name:
        if name not in outputs:
            _fail(f"no such output: {name}")
        value = outputs[name]
        console.print(value if isinstance(value, str) else json.dumps(value, default=str))
    else:
        console.print_json(json.dumps(outputs, default=str))


if __name__ == "__main__":
    app()
