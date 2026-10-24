# agent-microvm documentation

[README and quick start](README.md#quick-start) · [Demos](README.md#demo) · [Architecture](README.md#architecture)

Detailed setup and reference for the `vmnest` CLI, Python SDK, and MCP server.

## Contents

- [Components](#components)
- [Prerequisites](#prerequisites)
- [1. Install the SDK/CLI](#1-install-the-sdkcli)
- [2. Configure and deploy the infrastructure](#2-configure-and-deploy-the-infrastructure)
- [3. Use the SDK](#3-use-the-sdk)
- [4. Use the vmnest CLI](#4-use-the-vmnest-cli)
  - [Networking](#networking)
- [5. Serve a web app from a sandbox](#5-serve-a-web-app-from-a-sandbox)
- [Environment variables](#environment-variables)
- [Use it from an AI agent (MCP)](#use-it-from-an-ai-agent-mcp)
  - [Tools](#available-tools), [resources](#resources), and [configuration](#configuration)
- [Guest agent & lifecycle hooks](#guest-agent--lifecycle-hooks)
- [Bring your own guest image](#bring-your-own-guest-image)
- [Local guest-image smoke test](#local-guest-image-smoke-test)
- [Troubleshooting](#troubleshooting)
- [Status / not yet implemented](#status--not-yet-implemented)
- [Contributing](#contributing)
- [License](#license)

## Components

| Component | Description |
| --- | --- |
| Runtime | AWS Lambda MicroVMs (managed Firecracker) via `boto3` `lambda-microvms` |
| Guest agent | `guest/agentd` — FastAPI **agentd** (exec/fs API on port 8080) plus a **lifecycle-hook** server (port 9000), run together via `agentd.serve` and baked into the MicroVM image |
| SDK | `agent_sandbox.Sandbox.create(...)` |
| Transport | `agent_sandbox.agent_client.AgentClient` (HTTPS + `X-aws-proxy-auth`) |
| CLI | `vmnest` |
| Infrastructure | `vmnest infra` (bare `boto3` provisioner, `agent_sandbox.infra`) driven by `sandbox.yaml`; PyYAML ships in the base install |
| MCP server | `agent_sandbox_mcp` (FastMCP server, optional `[mcp]` extra, launched with `vmnest mcp`) |

## Prerequisites

- Python 3.11+; [`uv`](https://docs.astral.sh/uv/) is optional for source development
- Configured AWS credentials and a boto3/botocore installation that includes the
  `lambda-microvms` service model. AWS CLI v2 is useful for configuring and checking
  credentials; provisioning itself uses boto3.
- Docker (only to test the guest image locally; the build itself happens in AWS)
- A region where Lambda MicroVMs is available: `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1`. Set `region:` in `sandbox.yaml` for provisioning and use the same region for runtime commands (`--region` or `AWS_DEFAULT_REGION`). The infra config defaults to `us-east-1`; it does not read region environment variables.

`vmnest infra` uses the bundled `boto3` provisioner and a local JSON state file.

## 1. Install the SDK/CLI

Install **`vmnest`** from PyPI (Python import: `agent_sandbox`; command: `vmnest`):

```bash
pip install vmnest            # SDK + CLI + infra (PyYAML is a base dependency)
pip install "vmnest[mcp]"     # + MCP server (FastMCP) — see the MCP section below
pip install "vmnest[isal]"    # + faster ISA-L guest-image zipping (optional accelerator)
```

Or work from a clone with [`uv`](https://docs.astral.sh/uv/) (recommended for development):

```bash
git clone https://github.com/jnh12/agent-microvm.git
cd agent-microvm

uv sync                 # SDK + CLI + infra
uv sync --extra mcp     # + MCP server (FastMCP)
uv sync --extra isal    # + faster ISA-L guest-image zipping
# or editable with pip:
#   pip install -e .          # SDK + CLI + infra
#   pip install -e ".[mcp]"   # + MCP server
```

With `uv sync`, use `uv run vmnest ...` / `uv run python ...`, or activate
`.venv` before following the commands below. Verify with `vmnest --help`.

> Infrastructure support is included in the base install; the legacy `[infra]`
> extra is a no-op.

## 2. Configure and deploy the infrastructure

Configure resources in `sandbox.yaml`: set an existing ID/ARN to reuse a
resource, or leave it empty to create one.

```bash
vmnest infra init            # scaffold sandbox.yaml; skip if it already exists
vmnest infra preview         # see what will change
vmnest infra up              # create/update; prints outputs (image_arn, role, ...)
```

Edit the generated `sandbox.yaml` (a source checkout already includes one):

```yaml
project: agent-microvm
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
```

The default `image.guest_dir: ./guest` uses a local guest directory if available,
otherwise the guest source bundled with the package. Custom paths must exist and
contain a `Dockerfile`; relative paths resolve beside `sandbox.yaml`.

Provisioning is idempotent; resources and outputs are tracked in local state.
`role.extra_policy_arns` attaches managed policies to a newly created execution
role, letting guest code access services such as Secrets Manager, SSM, or S3
through IAM instead of embedding long-lived keys in the image. For a reused role
(`role.arn`), manage its permissions yourself.

An all-empty `network.egress` configuration leaves managed egress in place.
To enable VPC egress, set a connector ARN or at least one of `vpc_id`,
`subnet_ids`, `security_group_id`, or `operator_role_arn`; setting `name` alone
is insufficient. See [Networking](#networking).

Other infra commands: `vmnest infra refresh`, `vmnest infra destroy`,
`vmnest infra output [NAME]`. Use `vmnest infra up --rebuild` to force a new MicroVM
image version even when one is already active.

`vmnest infra output` labels outputs by project/stack (empty `{}` before
provisioning or after destruction). Select a config/stack with `-f <file>` /
`-s <stack>`, or list all with `vmnest infra output --all`.

### Local infrastructure state

Infrastructure state lives at `~/.agent_sandbox/infra-state.json` (override with
`AGENT_SANDBOX_INFRA_STATE`). It records outputs and resource ownership:
`vmnest infra destroy` removes managed resources and preserves reused ones.
AWS credentials are still required to provision resources.

## 3. Use the SDK

The CLI resolves image/role ARNs from infra outputs. For the Python SDK, pass
`image_arn` and `execution_role_arn` explicitly or export them first:

```bash
export AGENT_SANDBOX_IMAGE_ARN=$(vmnest infra output image_arn)
export AGENT_SANDBOX_EXECUTION_ROLE_ARN=$(vmnest infra output execution_role_arn)
python examples/run_code.py   # -> Hello from a microVM!
```

On Windows PowerShell, use `$env:` instead of `export`:

```powershell
$env:AGENT_SANDBOX_IMAGE_ARN = (vmnest infra output image_arn)
$env:AGENT_SANDBOX_EXECUTION_ROLE_ARN = (vmnest infra output execution_role_arn)
python examples/run_code.py
```

```python
import asyncio
from agent_sandbox import Sandbox

async def main():
    sandbox = await Sandbox.create("my-sandbox", cpus=1, memory=512)
    out = await sandbox.exec("python", ["-c", "print('hi')"])
    print(out.stdout_text)
    await sandbox.terminate()  # or stop() to suspend for later resume

asyncio.run(main())
```

## 4. Use the `vmnest` CLI

After `vmnest infra up`, `--image`/`--role` are auto-read from the stack, so most
commands need no flags:

```bash
# lifecycle (a named sandbox tracked in local state)
vmnest create app                    # auto-wired image/role from infra outputs
vmnest exec app -- python -c "import this"
vmnest ls                            # all tracked sandboxes + live status
vmnest ps app                        # one sandbox's status
vmnest inspect app                   # full MicroVM info as JSON
vmnest logs app                      # CloudWatch logs (best-effort)
vmnest metrics app                   # CloudWatch metrics (best-effort)
vmnest stop app                      # suspend (memory/disk preserved)
vmnest start app                     # resume
vmnest rm app                        # terminate + drop from local state

# ephemeral one-shot (image ARN is a required positional — not auto-wired)
vmnest run "$(vmnest infra output image_arn)" -- python -c "print('one-shot')"

# serve a port from inside the sandbox on localhost (see section 5)
vmnest forward app --remote-port 8000 --local-port 8000

# images
vmnest image build ./guest --name my-guest --bucket "$(vmnest infra output build_bucket)"
vmnest image ls                      # your images (--managed for AWS base images, --json for raw)
vmnest image rm <image-arn>

# infrastructure
vmnest infra init | preview | up | refresh | destroy | output [NAME]

# run the MCP server over stdio (see the MCP section)
vmnest mcp
```

**Common flags**

- `vmnest create`: `--image/-i`; `vmnest run` takes the image ARN as a positional
  argument. Both accept `--role`, `--cpus`, `--memory/-m` (default 512),
  `--region`, and `--egress-connector <arn>` / `--egress/-e` (use infra outputs).
  CPU/memory values are recorded by the SDK; actual resources are fixed by the
  image and are not changed by `run_microvm`.
- Every lifecycle/status command takes `--region`.
- `vmnest logs`: `--log-group` to override the CloudWatch group.
- `vmnest image ls`: `--managed` (AWS base images), `--json` (raw output).
- `vmnest forward`: `--remote-port/-r` (required), `--local-port/-l`,
  `--no-verify-tls`, `--poll-interval`. The reserved lifecycle-hook port
  (`AGENT_SANDBOX_HOOK_PORT`, default `9000`) is rejected — see
  [Guest agent & lifecycle hooks](#guest-agent--lifecycle-hooks).
- `vmnest infra up`: `--file/-f` is repeatable and accepts directories
  (multi-project), plus `--stack/-s`, `--rebuild`, `--parallelism/-p`.

`vmnest create` resolves image/role from the flag → env var
(`AGENT_SANDBOX_IMAGE_ARN` / `AGENT_SANDBOX_EXECUTION_ROLE_ARN`) → `vmnest infra`
outputs, in that order, so after `vmnest infra up` most commands need no flags.
`vmnest run` is the exception: it takes the image ARN as a required positional
argument and is **not** auto-wired.

The CLI keeps a local name → MicroVM map at `~/.agent_sandbox/state.json`
(override with `AGENT_SANDBOX_STATE`), since AWS has no concept of sandbox names.

> `image inspect` and `image prune` are available only as **MCP tools**, not as
> `vmnest` CLI commands.

### Networking

Lambda MicroVMs use **network connectors** (not subnets/security groups) for
ingress/egress, attached at `run_microvm` time. By default MicroVMs get managed
egress (e.g. `INTERNET_EGRESS`) from the image, so `vmnest create`/`run` need no
network config.

For custom VPC egress, provision a connector in `sandbox.yaml`'s `network.egress`
block (`vmnest infra up` creates/records `egress_network_connector_arn`), then
attach it per sandbox with `vmnest create app --egress` (pulls the connector from
infra outputs) or `--egress-connector <arn>`. The SDK accepts
`ingress_network_connectors` / `egress_network_connectors` directly for finer
control (e.g. `SHELL_INGRESS`).

VPC egress replaces the image's public egress; configure NAT/routing if the VPC
also needs internet access. Control outbound traffic with the connector's
security group. Creating a connector does not attach it to CLI-created
sandboxes until you pass `--egress` or `--egress-connector`.

MCP tools likewise require `egress=true` to use the configured connector, or an
explicit `egress_connector` ARN. Ingress connectors are opt-in through the SDK.

### CLI semantics

- `vmnest stop` **suspends** (resume with `vmnest start`); `vmnest rm` **terminates**.
- `vmnest image build` zips a directory, uploads it to S3, and registers a MicroVM image (there is no local OCI cache on AWS).
- `vmnest logs`/`vmnest metrics` read CloudWatch (best-effort; override the log group with `--log-group`).

## 5. Serve a web app from a sandbox

`vmnest forward` proxies a sandbox service to localhost.
[examples/serve_fastapi.py](examples/serve_fastapi.py) writes a FastAPI app,
starts `uvicorn`, and forwards it to `http://localhost:8000`:

```bash
python examples/serve_fastapi.py            # then open http://localhost:8000

# or forward a port for a service you started yourself:
vmnest forward app --remote-port 8000 --local-port 8000
```

## Environment variables

The CLI resolves image/role ARNs from flags, environment variables, or infra
outputs. The Python SDK requires explicit arguments or environment variables;
`vmnest run` requires the image ARN as a positional argument.

**Required values**

| Variable | Purpose |
| --- | --- |
| `AGENT_SANDBOX_IMAGE_ARN` | Image ARN for `vmnest create`, SDK, and MCP; `vmnest run` still requires its positional image ARN |
| `AGENT_SANDBOX_EXECUTION_ROLE_ARN` | Execution role ARN for the MicroVM |

AWS credentials and region come from the standard boto3 chain
(`AWS_DEFAULT_REGION`, `AWS_PROFILE`, instance role, etc.). Pass `region=` to the
SDK or `--region` to CLI commands to override the runtime region. Keep it aligned
with `region:` in `sandbox.yaml`; provisioning does not set the runtime region.
See [Prerequisites](#prerequisites).

**Optional overrides**

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_SANDBOX_REGION` | boto3 default | MCP region; raw SDK uses `region=`, CLI uses `--region` |
| `AGENT_SANDBOX_STATE` | `~/.agent_sandbox/state.json` | CLI name → MicroVM map |
| `AGENT_SANDBOX_INFRA_STATE` | `~/.agent_sandbox/infra-state.json` | `vmnest infra` state file |
| `AGENT_SANDBOX_SETUP` | `sandbox.yaml` | Infra config path (search order: `$AGENT_SANDBOX_SETUP` → `sandbox.yaml` → legacy `setup.yaml`) |
| `AGENT_SANDBOX_EGRESS_CONNECTOR` | from infra output | MCP connector ARN when `egress=true`; CLI uses `--egress` or `--egress-connector` |
| `AGENT_SANDBOX_WORKDIR` | `/work` | MCP default working directory inside the VM |
| `AGENT_SANDBOX_VERIFY_TLS` | `1` | MCP TLS verification; `0` disables it (debug only). Raw SDK uses `verify_tls=` |
| `AGENT_SANDBOX_AGENT_PORT` | `8080` | agentd application port the SDK scopes auth tokens to (must match the guest's `AGENTD_PORT`) |
| `AGENT_SANDBOX_HOOK_PORT` | `9000` | Reserved lifecycle-hook port; `vmnest forward` refuses it (must match the guest's `AGENTD_HOOK_PORT`) |

The MCP server adds a further set of `AGENT_SANDBOX_MCP_*` tuning knobs —
see [Configuration](#configuration) under the MCP section below.

## Use it from an AI agent (MCP)

The [MCP](https://modelcontextprotocol.io/) server (`agent_sandbox_mcp`) exposes
sandbox lifecycle, execution, files, logs, and metrics to AI agents. It uses
FastMCP over the Python SDK and follows
[microsandbox-mcp](https://github.com/superradcompany/microsandbox-mcp) tool conventions.

### Installation

Install the optional **`mcp` extra**; without it `vmnest mcp` exits and clients
report “Failed to connect.”

**From PyPI:**

```bash
pip install "vmnest[mcp]"
```

**Source checkout (development):**

```bash
uv sync --extra mcp
# or, with pip: pip install -e '.[mcp]'
```

**Global tool (so `vmnest` is on your `PATH` everywhere)** — include the `[mcp]`
extra:

```bash
# from a source checkout (editable — tracks your working tree):
uv tool install --editable ".[mcp]" --force
# or from PyPI (published as `vmnest`):
uv tool install "vmnest[mcp]"
```

> Keep `[mcp]` when reinstalling a global tool; omitting it removes the MCP
> dependency and breaks `vmnest mcp`.

Verify it's installed and reachable:

```bash
vmnest mcp     # starts the stdio server (Ctrl+C to stop). An "mcp not available"
            # message instead means the [mcp] extra is missing — see above.
which vmnest   # e.g. ~/.local/bin/vmnest — use this absolute path if your MCP client
            # runs in a different environment and can't find vmnest on its PATH.
```

Provision the backend first (`vmnest infra up`) so image/role ARNs resolve, or set
`AGENT_SANDBOX_IMAGE_ARN` / `AGENT_SANDBOX_EXECUTION_ROLE_ARN` (see
[Environment variables](#environment-variables)) — otherwise the client connects
but tool calls error at runtime.

Launch over stdio with `vmnest mcp`, `vmnest-mcp`, or
`python -m agent_sandbox_mcp`. If the client cannot find `vmnest`, use its absolute
path or `uv run --directory /path/to/agent-microvm vmnest mcp`. Set ARNs in the
client's `env` block if they cannot be resolved from infra state.

**Claude Code**

```bash
claude mcp add agent-sandbox -- vmnest mcp
```

**Claude Desktop** — add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "agent-sandbox": {
      "command": "vmnest",
      "args": ["mcp"]
    }
  }
}
```

**Cursor** — add to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "agent-sandbox": {
      "command": "vmnest",
      "args": ["mcp"]
    }
  }
}
```

**VS Code** — add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "agent-sandbox": {
      "command": "vmnest",
      "args": ["mcp"]
    }
  }
}
```

**Windsurf** — add to `~/.codeium/windsurf/mcp_config.json` (same `mcpServers`
shape as Cursor/Claude Desktop above).

**Goose** — Goose uses a YAML config. Either run `goose configure` → *Add
Extension* → *Command-line Extension* and enter `vmnest mcp`, or add an entry to
`~/.config/goose/config.yaml`:

```yaml
extensions:
  agent-sandbox:
    enabled: true
    type: stdio
    cmd: vmnest
    args: [mcp]
```

**pi / any other stdio client** — most clients share the same shape. This
generic block also shows wiring the required ARNs via `env` when they aren't
exported in the client's environment:

```json
{
  "mcpServers": {
    "agent-sandbox": {
      "command": "vmnest",
      "args": ["mcp"],
      "env": {
        "AGENT_SANDBOX_IMAGE_ARN": "arn:aws:lambda:us-east-1:...:microvm-image/...",
        "AGENT_SANDBOX_EXECUTION_ROLE_ARN": "arn:aws:iam::...:role/agent-sandbox-exec"
      }
    }
  }
}
```

### Available Tools

Every tool returns a JSON envelope: `{ "ok": true, "data": ... }` on success or
`{ "ok": false, "error": { "code", "message", ... } }` on failure. Large command
output, logs, and file reads are capped by default and include truncation
metadata (`truncated`, `total_bytes`, `returned_bytes`) when shortened.

**Runtime**

| Tool | Description |
| --- | --- |
| `runtime_check` | Check boto3, the `lambda-microvms` client, AWS credentials, and resolved image/role ARNs |
| `runtime_install` | Explain how to provision the backend (`vmnest infra up`); reports current status |
| `infra_outputs` | Pull image/role/region/egress from `vmnest infra` outputs (labeled with `project`/`stack`) so you needn't paste them; target a stack with `project=` / `setup_file=` |
| `infra_list` | List every provisioned `project/stack` in local state (for multi-`sandbox.yaml` setups) |

**Sandbox Lifecycle**

| Tool | Description |
| --- | --- |
| `sandbox_run` | Create an ephemeral sandbox, run a shell command, return output, and remove it |
| `sandbox_create` | Create and boot a persistent, named sandbox tracked in local state |
| `sandbox_start` | Resume a stopped (suspended) sandbox |
| `sandbox_list` | List tracked sandboxes with live status |
| `sandbox_status` | Show status for one sandbox or all tracked sandboxes |
| `sandbox_inspect` | Return full control-plane configuration/metadata for one sandbox |
| `sandbox_stop` | Suspend a sandbox (preserves state) |
| `sandbox_remove` | Terminate a sandbox and remove it from local state |
| `sandbox_wait` | Wait until a sandbox reaches a terminal or target state |

**Command Execution**

| Tool | Description |
| --- | --- |
| `sandbox_exec` | Execute an argv command with cwd, env, and timeout |
| `sandbox_shell` | Execute a shell command string (`bash -lc`) with the same controls |

**Logs**

| Tool | Description |
| --- | --- |
| `sandbox_logs_read` | Read captured CloudWatch logs with tail, since, and grep filters |
| `sandbox_logs_stream` | Poll captured logs using a cursor and a bounded follow timeout |

**Filesystem**

| Tool | Description |
| --- | --- |
| `sandbox_fs_read` | Read a sandbox file as UTF-8 text or base64 bytes |
| `sandbox_fs_write` | Write UTF-8 text or base64 bytes to a sandbox file |
| `sandbox_fs_list` | List sandbox directory entries |
| `sandbox_fs_mkdir` | Create a sandbox directory |
| `sandbox_fs_remove` | Remove a sandbox file or directory |
| `sandbox_fs_copy` | Copy a file or directory within a sandbox |
| `sandbox_fs_rename` | Rename/move a sandbox file or directory |
| `sandbox_fs_stat` | Get sandbox path metadata |
| `sandbox_fs_exists` | Check whether a sandbox path exists |
| `sandbox_fs_copy_from_host` | Copy an allowlisted host path into a sandbox |
| `sandbox_fs_copy_to_host` | Copy a sandbox path to an allowlisted host destination |

**Metrics**

| Tool | Description |
| --- | --- |
| `sandbox_metrics` | Get point-in-time CPU/memory metrics for one sandbox |
| `sandbox_metrics_all` | Get point-in-time metrics for all tracked sandboxes |
| `sandbox_metrics_stream` | Collect a bounded number of metric samples from one sandbox |

**Images**

| Tool | Description |
| --- | --- |
| `image_list` | List account images (or `managed=true` base images) |
| `image_inspect` | Inspect a MicroVM image by ARN |
| `image_remove` | Delete an image, guarded by `confirm: true` |
| `image_prune` | Remove images unreferenced by any live MicroVM, guarded by `confirm: true` |

### Resources

| URI | Description |
| --- | --- |
| `agent-sandbox://runtime` | Runtime/config status |
| `agent-sandbox://infra` | Resolved `vmnest infra` outputs (image/role/region/egress, labeled with project) |
| `agent-sandbox://sandboxes` | Current sandbox inventory |
| `agent-sandbox://images` | Current account image inventory |
| `agent-sandbox://policy` | Effective host-path and dangerous-operation policy |
| `agent-sandbox://schemas/sandbox-create` | JSON Schema for `sandbox_create` inputs |

### Configuration

The server reads the variables in
[Environment variables](#environment-variables) (image/role ARN, region, egress
connector, TLS, workdir) and adds these MCP-specific tuning knobs. If you run a
**custom guest image**, also see
[Bring your own guest image → If you use MCP](#if-you-use-mcp-with-a-custom-image)
for the env values the MCP process must resolve independently:

| Env var | Default | Description |
| --- | --- | --- |
| `AGENT_SANDBOX_MCP_HOST_PATHS` | current working directory | `os.pathsep`-separated allowlist for host copy operations |
| `AGENT_SANDBOX_MCP_HOST_PATH_POLICY` | `allowlist` | Set to `unrestricted` to allow any host path |
| `AGENT_SANDBOX_MCP_ENABLE_DANGEROUS` | `0` | Reserved for future dangerous ops; destructive image ops still require `confirm: true` |
| `AGENT_SANDBOX_MCP_MAX_OUTPUT_BYTES` | `1048576` | Default cap for command output, logs, and file reads |
| `AGENT_SANDBOX_MCP_DEFAULT_TIMEOUT_MS` | `120000` | Default timeout for exec-style operations |
| `AGENT_SANDBOX_IMAGE_ARN` | from `vmnest infra` | MicroVM image ARN |
| `AGENT_SANDBOX_EXECUTION_ROLE_ARN` | from `vmnest infra` | Execution role ARN |
| `AGENT_SANDBOX_REGION` | AWS default | AWS region |
| `AGENT_SANDBOX_EGRESS_CONNECTOR` | from `vmnest infra` | VPC egress network connector ARN to attach |
| `AGENT_SANDBOX_WORKDIR` | `/work` | MCP default working directory inside the VM |
| `AGENT_SANDBOX_VERIFY_TLS` | `1` | MCP TLS verification; `0` disables it (debug only). Raw SDK uses `verify_tls=` |

### SDK Gaps

The backend does not provide these capabilities:

- **Volumes** (`volume_*`) — no named-volume API.
- **Snapshots** (`snapshot_*`) — only suspend/resume exist, not content snapshots.
- **SSH / SFTP** (`sandbox_ssh_*`, `sandbox_sftp_*`) — no SSH subsystem in the guest.
- **Streaming exec sessions** (`sandbox_exec_start` / `_poll` / `_write_stdin` /
  `_signal` / `_close`) and `sandbox_drain` — `agentd`'s `/v1/exec` is a blocking,
  one-shot call, so interactive/streamed sessions are not possible without
  guest-side changes.

### MCP development

```bash
uv sync --extra dev --extra mcp     # installs mcp + deps
uv run pytest tests/mcp -q          # unit + stdio smoke tests (no AWS)
AGENT_SANDBOX_MCP_E2E=1 uv run python tests/mcp/e2e.py   # live e2e (needs infra)
```

## Guest agent & lifecycle hooks

The guest image runs **two servers in one process** (`python -m agentd.serve`):

- **agentd** on port `8080` — the application API (`/v1/exec`, `/v1/fs/*`,
  `/healthz`) the SDK/CLI reach through the MicroVM's auth-token proxy.
- **lifecycle hooks** on port `9000` — called **only by the AWS Lambda MicroVMs
  platform** (never by your clients) at image build and MicroVM state
  transitions.

Lifecycle behavior:

| Hook | When | What it does for you |
| --- | --- | --- |
| `/ready` | during image build, before the snapshot | waits until agentd is fully serving, so the snapshot captures a ready agent — no first-`exec` race, faster starts |
| `/validate` | after build, on a test VM | smoke-tests the snapshot so a broken image fails the build instead of shipping |
| `/run` | once per VM, on start from snapshot | mints a fresh per-VM identity and reseeds randomness (fixes the shared-snapshot uniqueness pitfall) |
| `/resume` | after `vmnest start` (resume) | reseeds randomness / refreshes per-VM state |
| `/suspend` | before `vmnest stop` suspends | drains any in-flight `exec` so commands aren't frozen mid-run |
| `/terminate` | before `vmnest rm` tears down | flushes logs; best-effort cleanup |

Hooks are baked into the image at build time, so **rebuild to pick them up on an
existing stack**: `vmnest infra up --rebuild`.

**The hook port is reserved.** `vmnest forward` refuses `--remote-port 9000` — the
lifecycle plane is the platform's private channel and must never be
client-reachable, and auth tokens are scoped to the app port only.

**Ports are configurable** (defaults `8080` / `9000`). Override the guest with
`AGENTD_PORT` / `AGENTD_HOOK_PORT` (baked via the Dockerfile) and the SDK/CLI with
the matching `AGENT_SANDBOX_AGENT_PORT` / `AGENT_SANDBOX_HOOK_PORT`. Keep the two
hook-port values in sync — the SDK tells the platform which port to call, and the
guest must bind the same one.

## Bring your own guest image

Set `image.guest_dir` in `sandbox.yaml` and run `vmnest infra up --rebuild`,
or build a directory containing a `Dockerfile` directly:

```bash
vmnest image build ./my-guest --name my-guest \
  --bucket "$(vmnest infra output build_bucket)" \
  --role "$(vmnest infra output execution_role_arn)"
```

Custom images must implement the same HTTP agent contract for SDK, CLI, and MCP
execution/filesystem tools:

1. **Serve the agentd API on the agent port.** Your image must answer
   `POST /v1/exec`, `POST /v1/fs/read`, `POST /v1/fs/write`, and `GET /healthz`
   on `AGENTD_PORT` (default `8080`). Easiest path: base your `Dockerfile` on the
   bundled guest, or `COPY` the bundled `agentd` package + `serve.py` into your
   image and keep its `CMD` (`python -m agentd.serve`).
2. **Keep the agent port aligned on both sides.** The guest binds `AGENTD_PORT`;
   the SDK/CLI/MCP scope auth tokens to `AGENT_SANDBOX_AGENT_PORT`. If you change
   one, change the other — a mismatch means the proxy can't route to your agent.
3. **Lifecycle hooks are opt-in.** To keep them, run `agentd.serve` (it starts
   the hook server too), `EXPOSE` + bind `AGENTD_HOOK_PORT` (default `9000`), keep
   `AGENT_SANDBOX_HOOK_PORT` in sync, and build through `vmnest infra up --rebuild`,
   which registers the hooks. `vmnest image build` has no `--hooks` option and
   does not register them. Without hooks you lose the startup checks and
   per-VM identity handling described above.

### If you use MCP with a custom image

MCP runs in the client process environment, which may differ from your shell.
Make custom-image settings available through the client's `env` block (see
[MCP setup](#use-it-from-an-ai-agent-mcp)):

| Env var | Must match | Why |
| --- | --- | --- |
| `AGENT_SANDBOX_IMAGE_ARN` | your custom image's ARN | MCP creates sandboxes from it |
| `AGENT_SANDBOX_EXECUTION_ROLE_ARN` | your execution role | assumed by the MicroVM |
| `AGENT_SANDBOX_AGENT_PORT` | the port your agent binds (`AGENTD_PORT`) | MCP scopes exec/fs auth tokens to it |
| `AGENT_SANDBOX_HOOK_PORT` | your guest's `AGENTD_HOOK_PORT` | keeps `vmnest forward` guarding the right port |

For MCP connection errors, check that token and guest ports match. Keep the
defaults (`8080` / `9000`) unless your image requires different ports.

## Local guest-image smoke test

You can exercise `agentd` and the hook server without AWS:

```bash
docker build --platform linux/arm64 -t agent-sandbox-guest ./guest
docker run --rm -p 8080:8080 -p 9000:9000 agent-sandbox-guest
curl localhost:8080/healthz
curl -s localhost:8080/v1/exec -H 'content-type: application/json' \
  -d '{"command":"python","args":["-c","print(1+1)"]}'

# lifecycle-hook server (normally the platform calls these, not you):
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  localhost:9000/aws/lambda-microvms/runtime/v1/ready   # -> 200 once agentd is up
```

## Troubleshooting

- **Missing image/role ARN or empty infra outputs:** run `vmnest infra output`
  from the directory containing your config, or set `AGENT_SANDBOX_SETUP` to its
  absolute path. Provision with `vmnest infra up` if needed. For the raw Python
  SDK, export both ARNs or pass them explicitly; for `vmnest run`, supply the
  image ARN positionally. Keep runtime and provisioning regions aligned.
- **Cannot create a `lambda-microvms` client:** check that the Python environment
  running `vmnest` has a boto3/botocore service model for `lambda-microvms`, valid
  AWS credentials, and access in the selected region. Upgrading an unrelated AWS
  CLI installation does not update the Python package's service models.
- **MCP “Failed to connect”:** install `vmnest[mcp]` in the environment the client
  launches. Run `vmnest mcp` directly to inspect startup errors. Use an absolute
  executable path if the client cannot find it; preserve `[mcp]` when reinstalling.
- **MCP connects but tools fail:** use `runtime_check` to inspect credentials and
  resolved ARNs. Set ARNs, `AGENT_SANDBOX_SETUP`, and region in the client's `env`
  when its working directory or environment differs from your shell. For custom
  images, check [matching ports](#if-you-use-mcp-with-a-custom-image).
- **Custom image fails to build or execute:** verify `image.guest_dir` resolves to
  a directory with a `Dockerfile`, implement the [agent contract](#bring-your-own-guest-image),
  and rebuild after changing guest code or hooks. Use the
  [local smoke test](#local-guest-image-smoke-test) to check the guest without AWS.
- **No internet after enabling VPC egress:** check connector attachment, security
  groups, routes, and NAT; see [Networking](#networking).
- **Port forwarding rejected or unreachable:** the lifecycle-hook port is
  reserved. Forward the application's port, ensure its server is running, and
  use `vmnest logs app` / `vmnest inspect app` to inspect the sandbox.

## Status / not yet implemented

Volumes, PTY/interactive sessions, network policy / TLS-MITM, and streaming exec
(HTTP/2 / WebSocket) are not yet implemented. See `sdk/agent_sandbox/` for
extension points, and [SDK Gaps](#sdk-gaps) for how these surface (or don't) in
the MCP server.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set
up a dev environment, run the tests and linter, and open a pull request.

## License

Apache-2.0 — see [LICENSE](LICENSE).
