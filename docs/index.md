---
title: AI agent sandboxes on AWS
description: Run untrusted Python and shell commands in your own AWS account with agent-microvm, the asbox Python package, and an MCP sandbox server.
---

# AI agent sandboxes on AWS

agent-microvm gives each agent an isolated AWS Lambda MicroVM for code execution,
file operations, and development workloads. Install `asbox`, import
`agent_sandbox`, or use the `asb` CLI. The MCP server exposes sandbox tools to
clients such as Claude Code and Cursor.

## Start with your task

- [Run untrusted Python code in an AWS sandbox](run-untrusted-python.md): install,
  provision, execute a command, and remove the sandbox.
- [Connect Claude Code and Cursor through MCP](mcp-sandbox.md): configure your
  client, verify the backend, and run a first sandbox task.
- [Compare agent-microvm, E2B, and microsandbox](compare-sandboxes.md): choose a
  deployment model and understand compatibility limits.
- [AWS permissions, costs, and cleanup](aws-permissions-costs-cleanup.md): scope
  credentials and understand which resources remain after a session.

## Before you start

You need Python 3.11+, AWS credentials, and access to Lambda MicroVMs in your
chosen region. This project provisions resources in your AWS account and AWS
charges apply. It does not provide a local/offline microVM backend.

See the [repository README](https://github.com/jnh12/agent-microvm#prerequisites)
for current prerequisites, full CLI/SDK reference, demo recordings, and
unsupported features.
