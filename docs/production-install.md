# VMNest

Manage cloud computers and Codex or Claude agents from your terminal.

```sh
pip install --upgrade vmnest
vmnest login
vmnest create my-computer
vmnest ls
vmnest shutdown my-computer
```

Python 3.11 or later is required. Hosted mode is the default: no AWS credentials,
API URL, or setup command is needed. Login opens https://vmnest.cc and uses
https://api.vmnest.cc. Credentials are stored in your operating system's keyring;
Linux requires an unlocked Secret Service or KWallet credential store.

Use `vmnest --help` for commands and `vmnest schema` for machine-readable discovery.
The CLI supports bulk computer operations, command execution, file transfers,
agent conversations, profiles, environment defaults, webhooks and event endpoints.
Use `--json` where supported for automation. Account limits and usage apply.

Connect an MCP client without installing a local MCP server at
https://vmnest.cc/dashboard/mcp. The remote MCP URL is https://api.vmnest.cc/mcp.

## Self-hosted and custom services

```sh
vmnest setup --mode self-hosted
vmnest infra --help
```

Self-hosted mode uses your AWS credentials and infrastructure. Its local MCP
server is available with `pip install 'vmnest[mcp]'` and `vmnest mcp`.

Explicit `--url`, `VMNEST_API_URL`, and saved service settings override the
production API default. `vmnest login --website URL` selects a development or
custom website. Existing overrides are preserved. To return to production:

```sh
vmnest setup --mode hosted --url https://api.vmnest.cc
vmnest login --website https://vmnest.cc
```

Source: https://github.com/jnh12/agent-microvm
