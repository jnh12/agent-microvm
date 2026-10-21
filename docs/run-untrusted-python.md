---
title: Run untrusted Python code in an AWS sandbox
description: Install asbox and run Python in an isolated AWS microVM using the asb CLI, with explicit setup and cleanup steps.
---

# Run untrusted Python code in an AWS sandbox

Use agent-microvm when an AI agent or application needs to execute generated
code outside your local machine. Each sandbox runs in your AWS account.
Isolation does not remove permissions you grant through IAM or network access;
read the [permissions guide](aws-permissions-costs-cleanup.md) first.

## Install and provision

You need Python 3.11+, configured AWS credentials, and an AWS CLI version and
region supporting Lambda MicroVMs. Check the
[current prerequisites](https://github.com/jnh12/agent-microvm#prerequisites).
Use a dedicated working directory for this configuration.

```bash
pip install asbox
asb --help
asb infra init
```

Edit the generated `sandbox.yaml`: choose your region and review the execution
role, image, and bucket configuration. If a configuration already exists, review
it instead of initializing another. Then:

```bash
asb infra preview
asb infra up
```

The preview shows planned infrastructure actions; `up` creates or reuses AWS
resources and records their identifiers locally. AWS charges apply.

## Execute Python

```bash
asb create python-demo
asb exec python-demo -- python -c "print(sum([1, 2, 3]))"
```

The Python command prints `6`. Replace it with the workload you want to execute.
This example verifies basic execution; it is not an isolation or security test.

To preserve a session temporarily:

```bash
asb stop python-demo
asb start python-demo
asb exec python-demo -- python -c "print('resumed')"
```

`stop` suspends; it does not terminate the sandbox. See the README for current
lifetime limits. Interactive terminals and streamed execution are not supported.

## Clean up

```bash
asb rm python-demo
asb ls
```

The named sandbox should no longer appear in local tracking. Run `rm` even if an
earlier command failed after creating the sandbox. Shared infrastructure remains;
follow [backend cleanup](aws-permissions-costs-cleanup.md#cleanup) when finished.

For Python applications, see the existing
[SDK example](https://github.com/jnh12/agent-microvm/blob/main/examples/run_code.py).
To connect an agent, continue with the [MCP guide](mcp-sandbox.md).
