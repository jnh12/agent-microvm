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

Creation persists the name, original region, launch parameters, and a generated AWS
client token before calling AWS. Saved VM identities survive restarts. Deletion intent
is persisted before termination; failed deletions retry in the background. Interrupted
`run` VMs with known IDs are terminated on restart. Running commands are never resumed
or automatically executed again. Recovery skips currently active `run` requests.

Recovery runs at startup and periodically. Set `VMNEST_RECOVERY_INTERVAL_SECONDS`
(default 5, any finite positive number) to change the polling interval. It adds no
VM lifetime or usage limit. An OS file lock allows only one API process per database;
do not start multiple uvicorn workers or use separate databases for replicas. The lock
is released automatically if the process crashes. Existing databases migrate in place.

`create`, `exec`, and `run` accept `--request-key` before `--`. The CLI generates a key
if omitted and prints it on operational failure. HTTP bodies use `request_key`.
Retry with the same key and original input to retrieve a completed operation. An
incomplete execution returns `OUTCOME_UNKNOWN` and is not run again. A key reused for
different input returns `REQUEST_CONFLICT`. A create replay cannot recreate a terminated
VM; use a new key to intentionally create another. Terminated records are retained for
safe replay but hidden from `ls`; names can be reused with a new key. Delete is repeatable
while that name still identifies the same VM. After name reuse, it targets the new VM.

Completed execution results are cached in the local SQLite database, including command
output, which may contain secrets. Keep the database private. Command inputs are compared
using a hash rather than stored verbatim. There is currently no automatic history pruning.

There is one deliberately conservative boundary: if a crash or lost AWS response occurs
after launch starts but before its VM ID is saved, recovery marks it `UNKNOWN`. AWS's VM
lookup does not return the client token, so this version cannot reliably identify that
VM automatically. It neither guesses nor relaunches; manual AWS reconciliation is still
required. A reservation persisted before launch started can safely resume using its
stored configuration. Transport failures remain uncertain; inspect before making a new
request with a different key. Keep the SQLite file across restarts.

Run tests with `pip install -e ".[dev,api]"` followed by `pytest tests -q`.
