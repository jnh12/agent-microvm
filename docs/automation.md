# CLI automation

Use `vmnest schema` for an offline JSON description of commands, arguments, flags,
required values, and numeric bounds in the selected mode. Output has a
`schema_version` so consumers can detect future changes. Existing command names
and bulk operations are preserved.

For external event triggers that run a disposable computer from a saved profile,
see [Webhooks](webhooks.md). Management commands are available under `vmnest webhook`.

Use [`vmnest defaults`](cloud-defaults.md) to manage account environment secrets
and startup scripts shared with website Settings.

## Submit, observe, and control

```sh
vmnest create worker-one worker-two --agent codex --json --request-key batch-001
vmnest wait worker-one --json --timeout 300
vmnest agent send worker-one --prompt-file - --json --request-key task-001 < prompt.txt
vmnest agent wait worker-one --run RUN_ID --json --timeout 300
vmnest agent logs worker-one --run RUN_ID --json --after 0
vmnest agent cancel worker-one --run RUN_ID --json
vmnest shutdown worker-one worker-two --json
```

Use the explicit run ID from submission to avoid accidentally observing or
cancelling a different task. `send` returns after acceptance by default. Add
`--wait --timeout SECONDS` to wait for a result. A timeout stops local waiting;
it does not cancel or resubmit remote work. Retry uncertain submissions with the
same request key and inputs. Polling errors retain the accepted run ID and request
key when available. Bulk creation returns individual keys as well as its batch key.

`--prompt-file -` reads stdin as one prompt, including newlines. It also works with
`create`. Bulk creation sends that same prompt to every selected computer.

## Output contracts

Use the command's `--json` flag where listed in the schema. JSON task submissions,
waits, and log snapshots each emit one document. A log snapshot includes `run`,
`events`, and `cursor`. Explicit `agent logs --follow --json` emits newline-delimited
JSON with typed `events` records and a final `result` record. Resume using `--after`.
Log following is bounded by `--timeout` (default 300 seconds).

`exec --json` and `run --json` return `encoding: "base64"`, `stdout`, `stderr`, and
`exit_code`, preserving binary output. Hosted execution includes its request key.
Without JSON these commands retain normal stdout/stderr behavior. File upload and
download also support JSON success and operational-error responses.

Account and ordinary VM operational errors use `ok: false` and an `error` object
with `code` and `message`. Batch errors remain attached to their individual items.
Argument-parsing errors retain normal CLI usage text and exit status 2; do not
assume every nonzero result is JSON. Commands that expose no JSON flag retain
their documented text or streaming output.

## Exit status and unattended operation

For agent task waits: 0 means completed, 1 means failed/cancelled/interrupted or
an operational error, 3 means approval is needed, 4 means the local wait timed out,
and 5 means the queue is paused. A successful asynchronous submission only means
accepted. Shell execution preserves the remote command's exit status instead.
Bulk commands return 1 if any item fails and include all available outcomes.

Model/profile selection and credential connection do not prompt when stdin is
noninteractive. Supply model, integrations, credential source, and consent flags
explicitly. `agent chat` requires a terminal; use `agent send` for scripts.
Credential upload still requires explicit consent.

VM HTTP requests have a finite 240-second timeout, configurable through
`VMNEST_REQUEST_TIMEOUT_SECONDS`. Account and discovery requests retain their
existing bounded timeouts. Polling deadlines are checked between requests, so a
request already in flight can extend a polling wait until its transport timeout.

Human-oriented tables, chat, and normal shell output remain available. Server-side
limits, billing, approvals, and shutdown policies remain authoritative.
