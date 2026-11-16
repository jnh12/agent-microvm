# Outbound event endpoints

Create a receiver on the site's **Event endpoints** page, then select computers
or profiles under **Activate**. Profile subscriptions are captured for new
computers, including disposable webhook computers. Existing computers can change
subscriptions immediately without a restart. Delivery runs on the API host and
does not keep a computer awake.

These are outbound callbacks, separate from webhooks that start computers.
Receivers must use public HTTPS on port 443. Private, loopback, link-local and
metadata IPs are blocked. DNS is checked and the connection pinned on every
attempt, with TLS hostname verification. Redirects and environment proxies are
not followed. Use an Internet-accessible receiver, not a localhost URL.

## Events

| Type | Data |
| --- | --- |
| `machine.status` | Observed power, desired power, startup phase, agent/setup state |
| `machine.needs_attention` | Failed or interrupted startup/setup state |
| `agent.status` | Run status and identifiers |
| `agent.reply` | Recorded assistant text, run/session/request identifiers |
| `agent.completed` | Task completed, with latest recorded reply |
| `agent.failed` | Failed/interrupted task, error and latest reply if available |
| `agent.cancelled` | Cancelled task |
| `agent.needs_attention` | Reported waiting-for-approval or run error state |
| `command.completed` | Exec request key, command ID, exit code, base64 stdout/stderr |
| `command.failed` | Exec request key, command ID, and `outcome_unknown: true` |
| `endpoint.test` | Signed sample event; no computer is started |

Attention events report service/provider states. They do not use another model
to infer that a reply is asking a question. Autonomous provider permissions remain
unchanged. A nonzero command exit code still produces `command.completed` because
the command returned a result; use `exit_code` to distinguish success.

Only API/CLI exec requests produce command-result events. Interactive terminal
keystrokes, terminal setup, and internal VM maintenance commands do not. Supply
a request key to correlate a command with your request. Command arguments,
environment variables, credentials and raw provider requests are not included.
Replies/output can themselves contain sensitive text: activate only endpoints
that should receive that computer's output.

## Envelope

```json
{
  "version": 1,
  "id": "stable-delivery-id",
  "type": "agent.completed",
  "created_at": 1760000000.0,
  "machine": {
    "name": "support", "generation": "generation-id",
    "agent": "codex", "disposable": true
  },
  "data": {
    "run_id": "run-id", "session_id": "session-id", "request_key": "ticket-123",
    "status": "completed", "error": null, "approval_id": null,
    "reply": "Opened the requested pull request.", "reply_truncated": 0
  }
}
```

Use the public machine name and generation to distinguish replacement computers.
`reply` is null when no recorded text exists. Replies are limited to 32,000
characters. Each command output stream is limited to 44,000 base64 characters;
`output_truncated` indicates truncation. Full agent journals remain accessible
through the existing owner-authenticated run events API until permanently deleted.
`endpoint.test` has `machine: null`.

## Verification and delivery behavior

Creation and rotation return a signing secret once. The server stores it encrypted.
Requests include `X-VMNest-Event`, `X-VMNest-Delivery`, `X-VMNest-Timestamp`, and
`X-VMNest-Signature`. Verify against the **raw request body**, before parsing JSON:

```python
import hashlib, hmac, time

def verify(secret, timestamp, signature, raw_body):
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Deduplicate on the delivery ID. Persist receipt before returning a 2xx response,
then process asynchronously. Ordering is not guaranteed. Treat a terminal agent
state as terminal even if an older status event arrives afterward.

Machine/run transitions and replies are captured in the same database transaction
as their recorded source change. Command notifications are queued after the exec
result is recorded; a process failure in that short interval may require retrying
the exec request with the original request key to enqueue the result.

Queued deliveries survive API restarts and disposable computer removal. There
are up to 8 attempts with exponential backoff, capped at one hour between retries.
Any non-2xx or transport failure retries; delivery is not exactly once. All retries
retain the same delivery ID and body but get a fresh timestamp/signature.
Final delivery history is retained for 7 days; the API lists the latest 50.

Updates affect pending deliveries as well as future ones: they use the current URL
and signing secret when attempted. Disabling cancels pending delivery attempts;
enabling does not replay them. Deletion removes subscriptions and delivery history.
An already in-flight HTTP request cannot be recalled. Historical events are not
replayed when enabling a subscription. Limits: 20 endpoints per account and 10
subscriptions per computer/profile.

## API and CLI

Manage `/v1/event-endpoints` with GET/POST. PUT/DELETE a specific endpoint ID;
POST `/{id}/test` or `/{id}/rotate`; GET `/{id}/deliveries`. PUT updates the full
definition (`name`, `url`, `events`, `enabled`). All routes require owner auth.
GET/PUT `/v1/vms/{name}/event-endpoints` uses `{"endpoints":["endpoint-id"]}`.
Profile create/PATCH accepts `event_endpoints`, a list of owned endpoint IDs.

```sh
vmnest endpoint create orchestrator --url https://your-service.com/events --json
vmnest endpoint bind my-computer --endpoint orchestrator --json
vmnest agent profile update support --endpoint orchestrator --json
vmnest endpoint test orchestrator --json
vmnest endpoint deliveries orchestrator --json
vmnest endpoint update orchestrator --disabled --json
vmnest endpoint bind my-computer --clear --json
vmnest agent profile update support --clear-endpoints --json
```

`--endpoint` and `--event` can be repeated. Omitting `--event` during creation
subscribes to all production event types. Profile/VM selection replaces the
selection, so include every endpoint you want enabled.
