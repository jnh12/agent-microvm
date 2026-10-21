---
title: AWS sandbox MCP server for Claude Code and Cursor
description: Connect Claude Code or Cursor to agent-microvm to create AWS sandboxes, execute code, manage files, and clean up sessions through MCP.
---

# AWS sandbox MCP server for Claude Code and Cursor

The agent-microvm MCP server exposes sandbox creation, command execution, file
operations, and lifecycle tools. It runs locally over stdio; workloads execute
inside AWS microVMs.

## Prepare the backend

Follow [install and provision](run-untrusted-python.md#install-and-provision),
then install MCP support in the same Python environment:

```bash
pip install "asbox[mcp]"
asb mcp --help
```

The MCP client process must have access to `asb`, your AWS credential provider,
region, and infrastructure configuration/state. Do not paste AWS secret keys
into a shared client configuration. Use your configured AWS profile or role.

## Claude Code

From the configured project directory:

```bash
claude mcp add agent-sandbox -- asb mcp
```

If `asb` is not on the client's PATH, replace it with its absolute executable path.

## Cursor

Merge this entry into `.cursor/mcp.json` for your project, preserving other
servers already configured:

```json
{
  "mcpServers": {
    "agent-sandbox": {
      "command": "asb",
      "args": ["mcp"]
    }
  }
}
```

If the client starts in another working directory, set `AGENT_SANDBOX_SETUP` in
the server's `env` object to the absolute path of your `sandbox.yaml`. If needed,
set `AWS_PROFILE` to your configured profile and `AWS_DEFAULT_REGION` to the
backend's region. Restart or reconnect the client after configuration changes.

## Verify with a small task

Ask the client:

> Use agent-sandbox's runtime_check tool and report any missing backend settings.
> If ready, use sandbox_run to execute python -c "print(6 * 7)" in an ephemeral
> sandbox. Show stdout and confirm cleanup succeeded.

Expected result: successful runtime checks, command output `42`, and successful
ephemeral cleanup. A connected MCP server alone does not prove AWS execution
works. If cleanup fails, inspect the AWS account for the remaining microVM.

For persistent sessions, use `sandbox_create`, `sandbox_exec`, and
`sandbox_remove`. `sandbox_stop` only suspends a session.

## Troubleshooting and limits

- **Failed to connect:** confirm the `[mcp]` extra is installed and the client
  resolves the correct executable.
- **Missing image or role:** provision the backend and ensure the client can
  resolve the same configuration and state as your shell.
- **AWS access errors:** check the client's profile, region, and permissions.
- **Custom image errors:** match the agent ports and API contract described in
  the [README](https://github.com/jnh12/agent-microvm#bring-your-own-guest-image).

MCP tool access does not redirect every local command the coding agent might
run. Ask it to use these tools and configure the client's other tool permissions
separately. PTY sessions, streaming exec, named volumes, and arbitrary snapshots
are not implemented. See the
[full tool reference](https://github.com/jnh12/agent-microvm#available-tools).

[Permissions and cleanup](aws-permissions-costs-cleanup.md) · [All guides](index.md)
