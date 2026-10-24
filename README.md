<div align="center">

<img src="demo/header.gif" alt="A pixel robot enthusiastically shovels sand next to a laptop and a rubber duck inside its own tiny sandbox." width="768">

# agent-microvm

**Isolated AWS microVM sandboxes for AI agents.**

Python SDK · CLI · MCP server · AWS microVMs

[![GitHub stars](https://img.shields.io/github/stars/jnh12/agent-microvm?style=flat&color=f6c85f)](https://github.com/jnh12/agent-microvm/stargazers)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-71d6c5)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/vmnest?color=8bb8f8)](https://pypi.org/project/vmnest/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-8bb8f8)](https://www.python.org/downloads/)

[Getting started](#1-install-the-sdkcli) · [Demo](#demo) · [MCP setup](#use-it-from-an-ai-agent-mcp) · [Contributing](CONTRIBUTING.md)

</div>

Run untrusted AI-generated code, user submissions, and CI jobs in
[**AWS Lambda MicroVMs**](https://aws.amazon.com/lambda/lambda-microvms/).
Each Firecracker sandbox provides VM-level isolation, snapshot-based startup,
a dedicated HTTPS endpoint, and suspend/resume for up to 8 hours.
Infrastructure is provisioned with **bare `boto3`**, without an external IaC engine.

## Demo

**Install and provision** — install `vmnest[mcp]`, configure `sandbox.yaml`,
then run `vmnest infra up` to build the image and provision resources:

![Install and provision a sandbox with vmnest](https://raw.githubusercontent.com/jnh12/agent-microvm/main/demo/01-setup.gif)

**Boot and serve an app** — create a sandbox, start FastAPI with `vmnest exec`,
then use `vmnest forward` to reach it on localhost:

![Run a web app inside a sandbox with vmnest](https://raw.githubusercontent.com/jnh12/agent-microvm/main/demo/02-sandbox.gif)

## Why agent-microvm?

Run model-generated code in a dedicated microVM without managing a VM fleet:

- **Isolated** — per-session Firecracker VMs separate workloads from the host
  and other sessions.
- **Stateful** — snapshot-based startup and suspend/resume preserve memory and disk.
- **Serverless** — no clusters, nodes, or idle fleet to manage.
- **Integrated** — Python SDK, CLI, HTTPS endpoint, and MCP tools for agents.
- **Simple provisioning** — `boto3` creates resources and tracks them locally.

### Use cases


**Parallel agents.** give each coding session its own named, independently
suspendable sandbox; reuse one guest image across many VMs.

**Unattended execution.** run shell commands, package installs, and file edits
inside the VM, away from your local files.

**Controlled access.** configure VPC egress via `network.egress` and scope AWS
access with `role.extra_policy_arns`. See [Networking](#networking).

**Remote development.** expose a sandbox dev server on localhost with
`vmnest forward app -r 8000 -l 8000`. See
[Serve a web app from a sandbox](#5-serve-a-web-app-from-a-sandbox).

**Session lifecycle.** `vmnest stop` suspends, `vmnest start` resumes, and
`vmnest rm` terminates.

## Contents

- [Demo](#demo)
- [Why agent-microvm?](#why-agent-microvm)
  - [Use cases](#use-cases)
- [Components](#components)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [1. Install the SDK/CLI](#1-install-the-sdkcli)
- [2. Configure and deploy the infrastructure](#2-configure-and-deploy-the-infrastructure)
- [3. Use the SDK](#3-use-the-sdk)
- [4. Use the `vmnest` CLI](#4-use-the-vmnest-cli)
- [5. Serve a web app from a sandbox](#5-serve-a-web-app-from-a-sandbox)
- [Environment variables](#environment-variables)
- [Use it from an AI agent (MCP)](#use-it-from-an-ai-agent-mcp)
- [Guest agent & lifecycle hooks](#guest-agent--lifecycle-hooks)
- [Bring your own guest image](#bring-your-own-guest-image)
- [Local guest-image smoke test](#local-guest-image-smoke-test)
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

## Architecture

```mermaid
graph TD
    subgraph app [Your Application]
        SDK["agent_sandbox SDK / vmnest CLI"]
    end
    subgraph aws [lambda-microvms control plane]
        CP["run / suspend / resume / terminate + auth token"]
    end
    subgraph vm [MicroVM from snapshot]
        AG["agentd: exec, fs.read, fs.write (:8080)"]
        HK["lifecycle hooks: ready / validate / run / resume / suspend / terminate (:9000)"]
    end
    SDK -->|"boto3"| CP
    CP -->|"id + HTTPS URL"| SDK
    SDK -->|"HTTPS + X-aws-proxy-auth"| AG
    CP -->|"lifecycle hooks (platform-only)"| HK
```

## Prerequisites

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/)
- AWS CLI v2 recent enough to include `lambda-microvms`, with credentials configured
- Docker (only to test the guest image locally; the build itself happens in AWS)
- A region where Lambda MicroVMs is available: `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1`. Pick any of these via `region:` in `sandbox.yaml` or the standard `AWS_DEFAULT_REGION` / `AWS_REGION` environment variable.

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

Verify with `vmnest --help`.

> Infrastructure support is included in the base install; the legacy `[infra]`
> extra is a no-op.

## 2. Configure and deploy the infrastructure

Configure resources in `sandbox.yaml`: set an existing ID/ARN to reuse a
resource, or leave it empty to create one.

```bash
vmnest infra init            # scaffold sandbox.yaml (edit as needed)
vmnest infra preview         # see what will change
vmnest infra up              # create/update; prints outputs (image_arn, role, ...)
```

`sandbox.yaml` (created by `vmnest infra init`):

```yaml
project: vmnest
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

Provisioning is idempotent; resources and outputs are tracked in local state.

Other infra commands: `vmnest infra refresh`, `vmnest infra destroy`,
`vmnest infra output [NAME]`. Use `vmnest infra up --rebuild` to force a new MicroVM
image version even when one is already active.

`vmnest infra output` labels outputs by project/stack (empty `{}` before
provisioning or after destruction). Select a config/stack with `-f <file>` /
`-s <stack>`, or list all with `vmnest infra output --all`.

### State file (no account or token required)

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
    await sandbox.stop()

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

- `vmnest create` / `vmnest run`: `--image/-i`, `--role`, `--cpus`, `--memory/-m`
  (default 512), `--region`, and `--egress-connector <arn>` / `--egress/-e`
  (attach the egress connector from infra outputs).
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
| `AGENT_SANDBOX_IMAGE_ARN` | MicroVM image ARN used by `vmnest create` / `vmnest run` and the SDK |
| `AGENT_SANDBOX_EXECUTION_ROLE_ARN` | Execution role ARN for the MicroVM |

AWS credentials and region come from the standard boto3 chain
(`AWS_REGION` / `AWS_DEFAULT_REGION`, `AWS_PROFILE`, instance role, etc.). The
region must be one where Lambda MicroVMs is available (see Prerequisites).

**Optional overrides**

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_SANDBOX_REGION` | boto3 default | Region for SDK / MCP calls |
| `AGENT_SANDBOX_STATE` | `~/.agent_sandbox/state.json` | CLI name → MicroVM map |
| `AGENT_SANDBOX_INFRA_STATE` | `~/.agent_sandbox/infra-state.json` | `vmnest infra` state file |
| `AGENT_SANDBOX_SETUP` | `sandbox.yaml` | Infra config path (search order: `$AGENT_SANDBOX_SETUP` → `sandbox.yaml` → legacy `setup.yaml`) |
| `AGENT_SANDBOX_EGRESS_CONNECTOR` | from infra output | VPC egress network connector ARN to attach |
| `AGENT_SANDBOX_WORKDIR` | `/work` | Default working directory inside the VM |
| `AGENT_SANDBOX_VERIFY_TLS` | `1` | Set `0` to skip TLS verification to the MicroVM endpoint (debug only) |
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

The server reuses the SDK/CLI variables from
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
| `AGENT_SANDBOX_WORKDIR` | `/work` | Default working directory inside the VM |
| `AGENT_SANDBOX_VERIFY_TLS` | `1` | Set `0` to skip TLS verification to the MicroVM endpoint (debug only) |

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

Build any directory containing a `Dockerfile` with
`vmnest image build ./my-guest`, or set `image.guest_dir` in `sandbox.yaml`.
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
   `AGENT_SANDBOX_HOOK_PORT` in sync, and build with `--hooks`
   (`vmnest infra up` does this). Drop them and the sandbox still works — you just
   lose the faster-start / per-VM-uniqueness benefits (see above).

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
