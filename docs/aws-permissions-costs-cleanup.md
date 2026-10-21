---
title: AWS sandbox permissions, costs, and cleanup
description: Understand provisioning credentials, microVM execution roles, network access, and cleanup when running AI agent sandboxes in your AWS account.
---

# AWS sandbox permissions, costs, and cleanup

agent-microvm separates the machine running the SDK/CLI/MCP server from the
microVM executing code. Review both sides before running untrusted workloads.

## Provisioning credentials and execution roles

The local provisioner uses the standard boto3 credential chain to create or
reuse infrastructure. Its AWS permissions are separate from the microVM's
execution role. Scope that role to only the AWS resources the workload needs.
`role.extra_policy_arns` in `sandbox.yaml` attaches additional managed policies
to the execution role; broad policies give sandboxed code broad AWS access.

VM isolation does not revoke IAM permissions or protect data you deliberately
copy into a sandbox. Avoid putting long-lived credentials in guest images.
See [configuration details](https://github.com/jnh12/agent-microvm#2-configure-and-deploy-the-infrastructure).

## Network and host-file access

Default sandbox egress is managed by the image. For custom VPC egress, configure
`network.egress` and attach it with `asb create NAME --egress`. Security groups
control network destinations by supported network rules; they are not a general
domain-name allowlist or an application-level policy engine.

MCP host-file copy tools default to a working-directory allowlist. Configure
`AGENT_SANDBOX_MCP_HOST_PATHS` narrowly and avoid `unrestricted` host-path policy
for untrusted workloads. Other tools enabled in your agent client can still
access the host independently of this server.

## Costs

The package has no service subscription, but AWS resources incur charges.
Check current [AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/)
and any applicable storage, logging, and networking charges for your region.
`asb infra preview` shows planned actions; it is not a cost estimate.

Record resource identifiers and review AWS billing after experiments. Suspending
a sandbox is different from deleting it; do not interpret `asb stop` as complete
resource cleanup or a guarantee that every associated charge has stopped.

## Cleanup

First terminate each sandbox you created:

```bash
asb ls
asb rm python-demo
```

Replace `python-demo` with each actual sandbox name. Local tracking is not an
account-wide resource inventory; check AWS directly if local state was lost or
an operation failed.

When no sessions need this backend, from the directory containing its reviewed
`sandbox.yaml`:

```bash
asb infra destroy
asb infra output
```

`destroy` removes resources tracked as managed by this stack. It deliberately
preserves reused resources. Empty outputs indicate local stack state has been
cleared, not proof that the AWS account contains no remaining resources. Review
errors and verify the relevant microVMs, images, buckets, and networking resources
in AWS. Keep configuration and local state until cleanup is verified.

[Python quickstart](run-untrusted-python.md) · [All guides](index.md)
