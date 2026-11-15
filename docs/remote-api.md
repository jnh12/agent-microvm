# Remote CLI

The open-source package contains the SDK, guest agent, direct CLI/MCP, self-hosted
provisioning, and thin remote CLI transport. The separate `vmnest-service` repository
owns the HTTP backend, SQLite inventory, recovery, API dependencies, and service
infrastructure. Install/run the backend from that repository, not from this package.

Hosted mode signs in through the VMNest website by default; `login --device` uses
the GitHub device-code fallback. Sessions are stored in the OS credential store.
Use the hosted API URL below, or a loopback service URL for local development.

```sh
vmnest setup --mode hosted --url https://api.vmnest.cc
vmnest login
vmnest whoami
vmnest create demo --agent none --request-key example-create
vmnest wait demo
vmnest exec demo --request-key example-exec -- python3 -c "print('hello')"
vmnest upload demo input.txt /tmp/input.txt
vmnest download demo /tmp/input.txt result.txt
vmnest ls
vmnest inspect demo
vmnest stop demo
vmnest start demo
vmnest logs demo
vmnest metrics demo
vmnest forward demo --remote-port 8081 --local-port 8081
vmnest shutdown demo
vmnest wake demo
vmnest usage
vmnest run default -- python3 -c "print('ephemeral')"
```

Creation runs in the background; `wait` waits for readiness. `shutdown` saves a
persistent computer and stops compute; `open` wakes it again. `delete` permanently
removes the computer, its saved workspace, and conversation history.

Image, role, region, networking and log-group selection are backend-managed in remote
mode. `infra`, `image`, and MCP commands remain direct-only. Provision infrastructure
separately. Remote command execution preserves stdout/stderr and exit codes.

`create`, `exec`, and `run` accept optional request keys. If a response is lost, reuse
the same key and input to retrieve the original outcome without executing again. The
CLI prints an automatically generated key on operational failure. Unknown outcomes
are not automatically replayed. See the service repository for recovery behavior,
HTTP routes, database handling, and operator configuration.

Each account sees only its own VMs. Names and request keys are independent between
accounts. `vmnest usage` shows the plan, pooled machine-hours, and saved-machine
allowance returned by the service. Billing and limit enforcement belong to the backend.

Account commands support `--json`. Agents start `login --json`, open the returned
approval link for the user, then poll `login --resume --json` at the returned interval.
Session tokens never appear in output. `VMNEST_API_URL` overrides the saved service URL;
credentials are scoped to that URL. `VMNEST_CONFIG` selects a non-secret config file.
For direct AWS mode, unset `VMNEST_API_URL` and run `vmnest setup --mode self-hosted`.
Direct mode requires no VMNest login.
