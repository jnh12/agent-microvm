# Single-user remote API

This branch adds an account-free HTTP adapter to the existing VM SDK. Provision AWS
resources separately. There are no provisioning, image administration, login,
ownership, billing, or per-account quota endpoints. The server stores one shared VM
inventory in SQLite; the client does not use local VM state in remote mode.

## Run the backend

Install this checkout with `pip install -e ".[api]"`. Configure the server process:

```powershell
$env:AGENT_SANDBOX_IMAGE_ARN = "YOUR_EXISTING_IMAGE_ARN"
$env:AGENT_SANDBOX_EXECUTION_ROLE_ARN = "YOUR_EXISTING_RUNTIME_ROLE_ARN"
$env:AWS_DEFAULT_REGION = "eu-central-1"
$env:VMNEST_API_DB = "C:\path\to\vmnest-api.db"
vmnest-api
```

The service uses the server's normal AWS credential chain. The launcher binds to
`127.0.0.1:8767`. Interactive API documentation is available at `/docs`, with an
OpenAPI contract at `/openapi.json`. Browser-origin API calls are rejected; use a
terminal client. Do not expose this unauthenticated service publicly. For a separate
machine, use an authenticated SSH tunnel to its loopback listener. The API accepts
localhost Host headers only. No AWS resources are provisioned on service startup.

## Select remote or direct mode

```powershell
$env:VMNEST_API_URL = "http://127.0.0.1:8767"
vmnest create demo
vmnest exec demo -- python3 -c "print('hello')"
vmnest upload demo .\input.txt /tmp/input.txt
vmnest download demo /tmp/input.txt .\result.txt
vmnest ls
vmnest ps demo
vmnest inspect demo
vmnest logs demo
vmnest metrics demo
vmnest stop demo
vmnest start demo
vmnest forward demo --remote-port 8081 --local-port 8081
vmnest rm demo
vmnest run default -- python3 -c "print('ephemeral')"

# Return to direct AWS operation:
Remove-Item Env:VMNEST_API_URL
```

`run default` uses the backend image. Create uses the same configured image.
Arbitrary image/role/region/network/log-group overrides are rejected in remote mode.
The image determines CPU and memory, as in the existing SDK. `infra`, `image`, and
MCP commands remain direct-only. Remote MCP integration is not part of this branch.

Execution preserves executable/argument boundaries, stdout, stderr, and exit code.
It is buffered, not interactive. Files are binary-safe and buffered, using the existing
guest read/write API. Upload/download are available in both modes. Guest file management
can also use ordinary commands through `exec`. No extra file size or execution cap
has been added. SDK transport timeouts, AWS limits, and existing idle defaults still apply.

Forwarding reuses the existing HTTP/WebSocket proxy. The backend grants a short-lived
token scoped to the requested VM port; the CLI then connects directly to that guest
endpoint. It receives no AWS account credentials. The lifecycle-hook port is blocked.
This avoids relaying application traffic twice; the client must be able to reach the
guest endpoint. It is not arbitrary TCP forwarding. TLS verification stays enabled.

## HTTP operations

| Method and path | SDK-backed operation |
| --- | --- |
| `POST /v1/vms` | Create with `{ "name": "demo" }` |
| `GET /v1/vms` | List the backend inventory |
| `GET /v1/vms/{name}` | Inspect a tracked VM |
| `DELETE /v1/vms/{name}` | Terminate and remove its inventory entry |
| `POST /v1/vms/{name}/suspend` | Suspend |
| `POST /v1/vms/{name}/resume` | Resume |
| `POST /v1/vms/{name}/exec` | Execute with command, args, optional cwd/env/timeout |
| `POST /v1/run` | Create, execute, and terminate in server-side finally cleanup |
| `POST /v1/vms/{name}/files/read` | Read a guest path; returns base64 content |
| `POST /v1/vms/{name}/files/write` | Write a guest path with base64 content |
| `GET /v1/vms/{name}/logs` | Read latest CloudWatch stream |
| `GET /v1/vms/{name}/metrics` | Read existing CPU/memory metric summary |
| `POST /v1/vms/{name}/forward/{port}` | Obtain a VM/port-scoped forwarding grant |

The API calls SDK operations, never CLI functions or shell-generated commands.
The direct and remote paths share observability functions and the forwarding proxy.

## Failure behavior

The database reserves names before launch and stores the AWS identity before waiting
for readiness. Duplicate names fail instead of launching another VM. The shared SDK
uses a stable client token across launch retries. HTTP requests are never automatically
replayed. An error can mean the operation completed but its response was lost; inspect
before retrying.

This is deliberately not a durable job scheduler. If the server dies during creation,
a `CREATING` record without an ID may need manual AWS inspection. If it dies during
`run`, its cleanup cannot execute; check AWS and terminate the VM manually. Unresolved
reservations are retained rather than silently launching replacements. Normal command
failures and live-request disconnects do not depend on client-side cleanup. Keep the
SQLite file across server restarts. Concurrent requests cannot reuse a reserved name.

Run tests with `pip install -e ".[dev,api]"` followed by `pytest tests -q`.
