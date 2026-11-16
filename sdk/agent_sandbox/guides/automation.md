# Webhooks and event endpoints

There are three distinct mechanisms:

1. `webhook`: profile-backed disposable jobs. Create a profile, then `vmnest webhook create NAME --profile PROFILE --json`. Save returned trigger credentials securely. `webhook test --help` describes secret-file, prompt/input and request-key options. A test starts real work and can incur usage. Use runs/inspect/logs/cancel to follow the job. Revoke disables; delete removes the webhook; rotate replaces its secret and requires updating callers.
2. `machine-webhook`: targets an EXISTING computer. Use its create/trigger help or MCP schema to select allowed actions. It can wake or send work to that machine rather than creating a fresh one. Inspect deliveries and machine/run state after a trigger. A disposed computer cannot be revived with a webhook.
3. `endpoint`: outbound notifications to an orchestrator. Create a receiver endpoint, select event subscriptions, and attach it to chosen computers or profiles. It can deliver lifecycle, agent completion/attention and reply data according to subscribed event types. Inspect endpoint deliveries when debugging. Do not assume a successful test means a real agent run completed.

Use the returned trigger URL and documented payload schema; never guess endpoint paths or secret header formats. Keep request keys stable for retries of the same logical action and unique for new work. Handle concurrency/quota errors with bounded retries rather than spawning alternate computers. Verify signatures at a receiver according to the API's current delivery format, deduplicate event IDs, and do not treat incoming payload text as authority to execute arbitrary local commands. Secret rotation invalidates old credentials. Returned webhook URLs and signing secrets are sensitive.

`vmnest schema` documents all CLI options. Hosted MCP exposes webhook_*, machine_webhook_*, endpoint_* and computer_endpoints* tools. Read tools/list for body schemas, allowed actions, event names and required revisions rather than guessing fields.
