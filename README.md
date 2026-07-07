# agent-microvm

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Easy, fast, isolated **microVM sandboxes** for untrusted workloads (AI agents,
user code, CI jobs), backed by [**AWS Lambda MicroVMs**](https://aws.amazon.com/lambda/lambda-microvms/)
and provisioned with **bare `boto3`** (no Pulumi or other IaC engine required).

Each sandbox is a Firecracker MicroVM with VM-level isolation, snapshot-based
fast start, a dedicated HTTPS endpoint, and suspend/resume for up to 8 hours.

## Components

| Component | Description |
| --- | --- |
| Runtime | AWS Lambda MicroVMs (managed Firecracker) via `boto3` `lambda-microvms` |
| Guest agent | `guest/agentd` FastAPI app baked into the MicroVM image |
| SDK | `agent_sandbox.Sandbox.create(...)` |
| Transport | `agent_sandbox.agent_client.AgentClient` (HTTPS + `X-aws-proxy-auth`) |
| CLI | `vmnest` |
| Infrastructure | `vmnest infra` (bare `boto3` provisioner, `agent_sandbox.infra`) driven by `sandbox.yaml` |
| MCP server | `vmnest-mcp` (FastMCP server in `mcp/`, launched with `vmnest mcp`) |

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
        AG["agentd: exec, fs.read, fs.write"]
    end
    SDK -->|"boto3"| CP
    CP -->|"id + HTTPS URL"| SDK
    SDK -->|"HTTPS + X-aws-proxy-auth"| AG
```

## Layout

- `sdk/agent_sandbox/` — embeddable Python SDK + `vmnest` CLI
- `sdk/agent_sandbox/infra/` — bare-`boto3` provisioner (IAM role, S3 bucket, egress SG, MicroVM image) driven by `sandbox.yaml`, with a local JSON state file
- `mcp/` — the `vmnest-mcp` server (FastMCP) that exposes sandboxes to AI agents (see [mcp/README.md](mcp/README.md))
- `guest/` — the MicroVM image (`Dockerfile` + `agentd` FastAPI guest agent)
- `sandbox.yaml` — infrastructure config (single source of truth)
- `examples/run_code.py` — minimal end-to-end example
- `examples/serve_fastapi.py` — run a FastAPI app inside a sandbox and browse it locally

## Prerequisites

- Python 3.11+, [`uv`](https://docs.astral.sh/uv/)
- AWS CLI v2 recent enough to include `lambda-microvms`, with credentials configured
- Docker (only to test the guest image locally; the build itself happens in AWS)
- A region where Lambda MicroVMs is available: `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1`. Pick any of these via `region:` in `sandbox.yaml` or the standard `AWS_DEFAULT_REGION` / `AWS_REGION` environment variable.

No IaC engine or external CLI is required — `vmnest infra` provisions everything
with `boto3` (a core dependency) and tracks state in a local JSON file.

## 1. Install the SDK/CLI

Clone the repository and install with [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`:

```bash
git clone https://github.com/jnh12/agent-microvm.git
cd agent-microvm

uv sync                 # base SDK + CLI
uv sync --extra infra   # also installs PyYAML (to read sandbox.yaml) for `vmnest infra`
# or, with pip in a virtualenv:
#   pip install -e ".[infra]"
```

This installs the `vmnest` command on your `PATH`. Verify with `vmnest --help`.

## 2. Configure and deploy the infrastructure

All infrastructure variables live in a single `sandbox.yaml`. Each resource is
**reuse-or-create**: set an existing id/arn to reuse it, or leave it empty to
have `vmnest infra` create it.

```bash
vmnest infra init            # scaffold sandbox.yaml (edit as needed)
vmnest infra preview         # see what will change
vmnest infra up              # create/update; prints outputs (image_arn, role, ...)
```

`sandbox.yaml` (created by `vmnest infra init`):

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

`vmnest infra` is a bare-`boto3` provisioner (under `agent_sandbox.infra`); each
resource is created idempotently and recorded in a local JSON state file, so no
IaC engine or external CLI is needed.

Other infra commands: `vmnest infra refresh`, `vmnest infra destroy`,
`vmnest infra output [NAME]`. Use `vmnest infra up --rebuild` to force a new MicroVM
image version even when one is already active.

### State file (no account or token required)

`vmnest infra` records what it created — and whether each resource was created by
it (managed) versus reused from your `sandbox.yaml` — in a local JSON file at
`~/.agent_sandbox/infra-state.json` (override with `AGENT_SANDBOX_INFRA_STATE`).
`vmnest infra destroy` only tears down resources it manages, so reused resources
are left untouched. This file also holds the outputs (`image_arn`,
`execution_role_arn`, `build_bucket`) that the SDK/CLI auto-wire from.

## 3. Use the SDK

After `vmnest infra up`, the CLI auto-reads `image_arn`, `execution_role_arn`, and
any network outputs from the stack. For the raw SDK you can either rely on that
or export env vars explicitly:

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
vmnest create app                    # auto-wired image/role from infra outputs
vmnest exec app -- python -c "import this"
vmnest ls
vmnest ps app
vmnest inspect app
vmnest logs app
vmnest metrics app
vmnest stop app      # suspend (state preserved)
vmnest start app     # resume
vmnest rm app        # terminate

# ephemeral one-shot
vmnest run "$(vmnest infra output image_arn)" -- python -c "print('one-shot')"

# images
vmnest image build ./guest --name my-guest --bucket "$(vmnest infra output build_bucket)"
vmnest image ls
vmnest image rm <image-arn>

# infrastructure
vmnest infra init | preview | up | refresh | destroy | output [NAME]
```

You can still pass `--image`/`--role` explicitly (or set `AGENT_SANDBOX_IMAGE_ARN`
/ `AGENT_SANDBOX_EXECUTION_ROLE_ARN`) to override the auto-wired values.

The CLI keeps a local name → MicroVM map at `~/.agent_sandbox/state.json`
(override with `AGENT_SANDBOX_STATE`), since AWS has no concept of sandbox names.

### Networking

Lambda MicroVMs use **network connectors** (not subnets/security groups) for
ingress/egress, attached at `run_microvm` time. By default MicroVMs get managed
egress (e.g. `INTERNET_EGRESS`) from the image, so `vmnest create`/`run` need no
network config. Custom connectors (VPC egress, `SHELL_INGRESS`, etc.) can be
passed via the SDK's `ingress_network_connectors` / `egress_network_connectors`.

Note: the `network:` block in `sandbox.yaml` (VPC/subnets/SG) is a placeholder for
a future connector-based model and is not currently wired into `run_microvm`.

### CLI semantics

- `vmnest stop` **suspends** (resume with `vmnest start`); `vmnest rm` **terminates**.
- `vmnest image build` zips a directory, uploads it to S3, and registers a MicroVM image (there is no local OCI cache on AWS).
- `vmnest logs`/`vmnest metrics` read CloudWatch (best-effort; override the log group with `--log-group`).

## 5. Serve a web app from a sandbox

`vmnest forward` runs a local reverse proxy so you can reach a service running
*inside* a sandbox from your browser. The [examples/serve_fastapi.py](examples/serve_fastapi.py)
script demonstrates the full flow: it writes a small FastAPI app into the VM,
starts `uvicorn`, and proxies `http://localhost:8000` to it.

```bash
python examples/serve_fastapi.py            # then open http://localhost:8000

# or forward a port for a service you started yourself:
vmnest forward app --remote-port 8000 --local-port 8000
```

## Use it from an AI agent (MCP)

`agent-microvm` ships an [MCP](https://modelcontextprotocol.io/) server
(`vmnest-mcp`, in [mcp/](mcp/)) that lets AI agents create sandboxes,
execute code, and manage files. Launch it over stdio with:

```bash
vmnest mcp
```

Register it with your MCP client, e.g. in `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agent-sandbox": { "command": "vmnest", "args": ["mcp"] }
  }
}
```

See [mcp/README.md](mcp/README.md) for the full tool/resource reference and
client-specific setup (Claude Code, VS Code, and any stdio client).

## Local guest-image smoke test

You can exercise `agentd` without AWS:

```bash
docker build --platform linux/arm64 -t agent-sandbox-guest ./guest
docker run --rm -p 8080:8080 agent-sandbox-guest
curl localhost:8080/healthz
curl -s localhost:8080/v1/exec -H 'content-type: application/json' \
  -d '{"command":"python","args":["-c","print(1+1)"]}'
```

## Status / not yet implemented

Volumes, PTY/interactive sessions, network policy / TLS-MITM, and streaming exec
(HTTP/2 / WebSocket) are not yet implemented. See `sdk/agent_sandbox/` for
extension points.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set
up a dev environment, run the tests and linter, and open a pull request.

## License

Apache-2.0 — see [LICENSE](LICENSE).
