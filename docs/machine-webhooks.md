# Webhooks for existing computers

These are separate from profile webhooks, which create disposable computers.
Create one after creating a persistent computer. A webhook is bound to that computer,
not a reusable name: deleting and recreating the computer invalidates its old hooks.
Normal sleep and restore preserve the binding.

## CLI

```sh
vmnest machine-webhook create support --computer support-agent --action wake --action prompt --action shutdown --json
vmnest machine-webhook list --json
vmnest machine-webhook deliveries support --json
vmnest machine-webhook rotate support --json
vmnest machine-webhook revoke support --json
```

Creation returns the URL and a secret once. Store the secret securely. To test from
the CLI, set `VMNEST_WEBHOOK_SECRET` in your environment, then:

```sh
vmnest machine-webhook trigger support --action wake --request-key wake-001 --json
vmnest machine-webhook trigger support --prompt "Review this ticket" --input-json '{"ticket":42}' --request-key ticket-42 --json
vmnest machine-webhook trigger support --action shutdown --request-key stop-001 --json
```

`--secret-env` selects another environment variable. Do not put secrets in URLs.
`--session` chooses the agent conversation (default `main`).

## HTTP

POST the returned URL with `Authorization: Bearer <secret>`,
`Content-Type: application/json`, and `Idempotency-Key: <unique delivery key>`.

```json
{"action":"prompt","prompt":"Review this ticket","input":{"ticket":42},"session":"main"}
```

Actions: `wake`, `prompt`, `shutdown`. Each requires its permission on the webhook.
A prompt wakes the computer, waits for readiness, then queues the task. It does not
create another computer or automatically dispose of this one. Normal idle shutdown
still applies. Linux computers support wake and shutdown, not agent prompts.

The API returns HTTP 202 with a delivery ID. Acceptance does not mean the action
has completed. Deliveries survive API restarts; pending dispatch expires after
30 minutes. Retrying the same key and payload returns the same delivery. Reusing a
key with different input returns 409. Prompt dispatch uses a stable agent request key.
A repeated wake/shutdown during recovery is safe, but power operations are not an
exactly-once transaction with the cloud provider.

Delivery history shows pending/completed/failed or submitted with the agent run's
current status and ID. Use the existing agent run events API to read replies, or
activate an outbound Event endpoint on the computer for callbacks. API management:
GET/POST `/v1/machine-webhooks`, DELETE `/{id}`, POST `/{id}/rotate`,
GET `/{id}/deliveries`. Management requires account authentication; trigger only
accepts its dedicated secret. History returns the latest 50 deliveries.

Revocation blocks new and not-yet-dispatched work. Already-submitted agent tasks
continue; cancel them through the normal agent API. Rotation invalidates the old
secret but retains already-accepted deliveries. Limits: 50 active hooks per account,
100 pending deliveries per account, 64 KiB per request. Creating and testing are
also available under Website > Webhooks > Existing computers.
