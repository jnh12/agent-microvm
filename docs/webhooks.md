# Disposable computers from webhooks

A webhook launches a fresh computer using a saved agent profile, runs one task,
and terminates the computer when the task completes, fails, is cancelled, or
reaches its deadline. Agent events remain available afterward. Files inside the
computer are discarded: instructions should tell the agent to push its PR or
upload any output before finishing.

This requires the hosted API with webhook support. Connect your agent account
first, then create a profile with the integrations the task needs. The webhook
uses that account connection; credentials are not copied into the profile.

```sh
vmnest agent profile create support --provider codex --integration github --instructions-file support.txt --timeout 1800 --json
vmnest webhook create support-tickets --profile support --concurrency 1 --json
vmnest webhook update support-tickets --profile support --concurrency 2 --json
```

The website's Webhooks page supports the same configuration, secret rotation,
revocation, recent runs and a test delivery form. Test deliveries start real
disposable computers and consume normal quota. Existing secrets cannot be read
back; paste your saved secret or explicitly replace it.

To test from the CLI without exposing the secret in command arguments:

```sh
vmnest webhook test support-tickets --secret-file webhook-secret.txt --request-key test-001 --prompt "Run this test task" --input-file input.json --json
```

`--secret-file -` reads stdin. Reuse the delivery key after a timeout to avoid
creating a duplicate computer. Use a new key for a new task. Updating a webhook
does not change profile snapshots already captured by accepted deliveries.

Profiles can also supply a public repository, a startup script, and environment
keys, for both normal computers and webhook jobs:

```sh
vmnest agent profile update support --repo https://github.com/example/public-repo --setup-file startup.sh --env-file keys.json --json
vmnest agent profile set-key support API_KEY
vmnest agent profile set-key support API_KEY --file - --json
vmnest agent profile update support --unset-key API_KEY --clear-repo --clear-setup --json
```

**Repositories must be public HTTPS Git URLs.** Private repositories and embedded
credentials are not supported. The startup script runs once after cloning,
in `/workspace/project` (or `/workspace` without a repo), and accepts up to
32,000 characters. Account startup scripts run before cloning.

`keys.json` is a JSON object mapping names to string values. Profile keys override
matching account defaults. Saved values are encrypted and hidden from reads;
`inspect --json` returns their names only. `--env-file -` or `--setup-file -` reads
stdin. `set-key` prompts secretly by default; `--file -` supports automated input.
Removing a profile key restores any account default for that name. Profile edits
apply to new computers; accepted webhook jobs retain their captured settings.

Example `support.txt`:

```text
Investigate the supplied support ticket in the specified repository.
Make a focused fix, run relevant tests, and open a pull request.
Do not merge it. Finish by reporting the PR URL and test results.
```

Creation returns an ID, `trigger_url`, and a secret shown only once. Configure
your caller with that URL and secret. An example delivery using shell variables:

```sh
curl --request POST "$WEBHOOK_URL" \
  --header "Authorization: Bearer $WEBHOOK_SECRET" \
  --header "Idempotency-Key: ticket-123-v1" \
  --header "Content-Type: application/json" \
  --data '{"prompt":"Investigate this ticket and open a PR.","input":{"ticket":123,"repository":"org/repo","description":"Save button does nothing"}}'
```

The response is HTTP 202 with a job `id`, `computer`, `status`, `deadline`, and
`status_url`. Acceptance is durable but does not mean the computer is ready.
Use a stable delivery ID as `Idempotency-Key`: retries with the same input
return the original job; changing the input with that key returns HTTP 409.
Use a new key for a new execution, including an intentional retry of a failed task.

Only `prompt` (string) and `input` (object) are accepted. A prompt can be omitted
when the profile already has instructions. The JSON body is limited to 64 KiB.
Incoming input is appended as task context; it cannot directly override the
profile, integrations, setup command, or runtime settings.

Inspect results with your normal VMNest account login:

```sh
vmnest webhook runs WEBHOOK_ID --json
vmnest webhook inspect JOB_ID --json
vmnest webhook logs JOB_ID --after 0 --json
vmnest webhook cancel JOB_ID --json
```

Logs return a cursor for the next page. The trigger secret only permits new
deliveries and replay receipts, not access to account history or agent events.
For HTTP clients, `GET /v1/webhook-runs/JOB_ID` and its `/events?after=0`
endpoint require the normal account bearer token.

Jobs move through `queued`, `starting`, `running`, and `stopping`, then end as
`completed`, `failed`, `cancelled`, or `timed_out`. A job stays `stopping` while
termination is unconfirmed, and cleanup is retried after worker restarts.
Success means the agent reported task completion, not independent verification
that a PR was created. Consult its retained events for the result.

The default lifetime is 1,800 seconds; profiles accept 60 to 3,600 seconds using
`--timeout`. Queue and setup time count toward the deadline. AWS also receives
the profile's lifetime as a maximum VM duration, providing a fallback cap if
the service is unavailable. Worker polling and provider termination can delay
cleanup beyond the deadline. Each webhook allows 1 to 5 concurrent jobs
(default 1), subject to account quotas; at most 100 unfinished webhook jobs
may be accepted per account. This is a generic authenticated JSON endpoint,
not a native GitHub/Stripe signature verifier or event-envelope adapter.

Profiles can also store `--setup 'command'`, run before the agent, and
`--instructions 'text'`. Use `agent profile inspect NAME --json` to read all
settings and `agent profile update NAME` to change them. Empty instructions or
setup strings clear those values. Accepted deliveries retain their profile
snapshot; edits affect future deliveries. Account connections must remain valid
when execution starts. Computers run unattended with agent approvals disabled.

```sh
vmnest webhook list --json
vmnest webhook rotate WEBHOOK_ID --json
vmnest webhook revoke WEBHOOK_ID --json
```

Rotation immediately replaces the old secret. Revocation rejects new deliveries
and cancels queued jobs; active jobs finish. Cancel individual active jobs with
`webhook cancel JOB_ID`. Revoked definitions remain in history; create a new
webhook with a new name to replace one. No cloud infrastructure is provisioned
by creating a webhook definition.
