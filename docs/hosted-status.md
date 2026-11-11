# Hosted integration status

This branch adds GitHub login and HTTP routing to the existing self-hosted CLI.
The separate `vmnest-service` repository owns accounts, VM ownership, recovery,
and hosted infrastructure. Hosted MCP support is deferred.

The tested service endpoint is `https://api.vmnest.cc`. Use this branch's package:

```sh
uv sync
uv run vmnest setup --mode hosted --url https://api.vmnest.cc
uv run vmnest login
uv run vmnest whoami
uv run vmnest usage
uv run vmnest usage --json
```

An existing global `vmnest` executable may belong to a different release. In the
tested Windows environment it was version 0.3.0, defaulted to the website origin,
and returned website HTML on API calls. Installing this branch as the normal CLI
and simplifying hosted defaults remain follow-up work; no package release is made
by pushing this feature branch.

Usage is calculated by the service, scoped to the signed-in account, and defaults
to the current UTC month. Use `--from` and `--to` with timezone-aware ISO timestamps
for another period. Running and suspended times are observation-based estimates;
long gaps are reported as unknown and history before tracking is unavailable.
Usage adds no billing or account limits.

## Verified and outstanding

Live checks on 2026-11-11 covered browser login, creation, execution, file transfers,
suspend/resume, termination, and persistence across API restart. Test VMs were
confirmed terminated. The SDK suite passed 123 tests.

- Hosted `stop` and `rm` may print RUNNING immediately after AWS accepts the action.
  Polling confirms the later SUSPENDED or TERMINATED state. Completion reporting
  and configurable waiting need improvement.
- API errors are displayed as raw response text. Unexpected HTML should become a
  readable endpoint/configuration error.
- The live hosted logs request returned a generic 502 and needs investigation.
- Metrics returned no samples, currently displayed as `-`.
- The temporary Windows project launcher prints a uv warning on each invocation.

These issues are documented, not fixed by the publication commit. Billing and
account quotas are outside this integration's scope.
