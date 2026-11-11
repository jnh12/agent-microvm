# Remote CLI

The open-source package contains the SDK, guest agent, direct CLI/MCP, self-hosted
provisioning, and thin remote CLI transport. The separate `vmnest-service` repository
owns the HTTP backend, SQLite inventory, recovery, API dependencies, and service
infrastructure. Install/run the backend from that repository, not from this package.

Select remote mode with `VMNEST_API_URL=http://127.0.0.1:8767`; unset it for direct AWS
mode. Accounts are not required. The backend is single-user and loopback-only; use an
authenticated tunnel rather than exposing it publicly.

```sh
vmnest create demo --request-key example-create
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
vmnest rm demo
vmnest run default -- python3 -c "print('ephemeral')"
```

Image, role, region, networking and log-group selection are backend-managed in remote
mode. `infra`, `image`, and MCP commands remain direct-only. Provision infrastructure
separately. Remote command execution preserves stdout/stderr and exit codes.

`create`, `exec`, and `run` accept optional request keys. If a response is lost, reuse
the same key and input to retrieve the original outcome without executing again. The
CLI prints an automatically generated key on operational failure. Unknown outcomes
are not automatically replayed. See the service repository for recovery behavior,
HTTP routes, database handling, and operator configuration.
