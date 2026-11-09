# Hosted agents

VMNest runs Codex or Claude Code inside an isolated computer. A prompt job cleans
up its computer when finished. A durable agent keeps its conversation and files
between sessions. The hosted pilot requires operator activation and has daily
runtime limits. VMNest billing is disabled; your model provider's usage terms and
charges still apply.

## CLI

```sh
pip install -U vmnest
vmnest login

# Explicitly transfer only your existing Codex login credential.
vmnest credential connect codex --from-local
# Or connect a provider API key already present in your environment:
vmnest credential connect codex --api-key-env OPENAI_API_KEY
vmnest credential connect claude --api-key-env ANTHROPIC_API_KEY

vmnest credential verify CREDENTIAL_ID
vmnest profile create work --provider codex --credential CREDENTIAL_ID
vmnest agent run "Create a small Python project and run its tests" --profile PROFILE_ID
```

`--from-local` uploads the selected Codex `auth.json` login to VMNest's encrypted
credential store. `--auth-file PATH` selects a different file. It does not copy
your home directory, AWS credentials, plugins, or unrelated configuration. A
keyring-only Codex login needs a file-based Codex sign-in first. Claude managed
agents accept API keys; Claude subscription credentials are not imported.
Codex may refresh an imported login. Revoking that provider login affects VMNest
too; if the same login stops working locally after a refresh, sign in locally again.

Profiles grant no connections by default. To use connected Codex apps, discover
their metadata, inspect the available tools, and grant the exact tools needed:

```sh
vmnest credential discover CREDENTIAL_ID
vmnest connection tools CONNECTION_ID
# permissions.json: {"allowed_tools":["EXACT_TOOL_NAME"]}
vmnest connection update CONNECTION_ID --file permissions.json
vmnest profile create connected-work --provider codex --credential CREDENTIAL_ID \
  --connection CONNECTION_ID
```

An empty tool list denies every tool. `"*"` explicitly grants all tools in that
connection. Token-based GitHub connections (`--kind github`) also require exact
`owner/repository` entries and `--write` for write operations. Native GitHub app
connections grant the selected tools within the connected provider account's
access. Remote MCP connections use HTTPS and separately stored tokens or headers.
Connection destinations and credentials are immutable; create a new connection
to change them.

```sh
vmnest connection create source --kind github --repository owner/repo \
  --token-env GITHUB_TOKEN --tool get_file --tool list_files

vmnest agent create project --profile PROFILE_ID
vmnest agent chat AGENT_ID
vmnest agent stop AGENT_ID
vmnest agent start AGENT_ID

vmnest agent run "Run the test suite" --agent AGENT_ID --detach
vmnest run list
vmnest run events RUN_ID --follow
vmnest run cancel RUN_ID
vmnest run artifacts RUN_ID
vmnest run download-url RUN_ID ARTIFACT_ID
```

Stopping saves conversation, workspace files, and project dependencies installed
inside the workspace. Processes, RAM, and globally installed OS packages reset
when the computer stops. Idle agents stop automatically. A run uses a snapshot
of its profile;
editing a profile affects new agents, while an existing stopped agent can adopt
it with `vmnest agent update AGENT_ID --file settings.json`, where the file
contains `{"profile_id":"PROFILE_ID"}`.

## Webhooks

```sh
vmnest webhook create build --provider github --profile PROFILE_ID \
  --event push --prompt "Review this push: {{payload}}"
vmnest webhook list
vmnest webhook test WEBHOOK_ID --file sample-payload.json
vmnest webhook rotate WEBHOOK_ID
```

Create and rotate show the signing secret once. Store it in the event sender.
GitHub uses its standard `X-Hub-Signature-256` and delivery headers. Generic
webhooks require `X-VMNest-Timestamp`, a unique `X-VMNest-Delivery`, and
`X-VMNest-Signature: sha256=HEX_HMAC_SHA256(secret, timestamp + "." + raw_body)`.
The stored profile and prompt template control each run; incoming payloads cannot
replace them. Delivery IDs deduplicate retries, and daily trigger limits apply.

## Python

```python
from agent_sandbox import Agents

agents = Agents()  # saved CLI login or VMNEST_API_TOKEN
run = agents.run("Run tests", profile_id="PROFILE_ID", idempotency_key="my-job-2026-11-09")
for event in agents.follow(run["id"]):
    print(event["kind"], event["text"])
result = agents.get("runs", run["id"])

chat = agents.create("agents", name="project", profile_id="PROFILE_ID")
next_run = agents.message(chat["id"], "Add a README", idempotency_key="my-message-1")
agents.action("agents", chat["id"], "stop")
```

Reuse an idempotency key when retrying the same submission. `list`, `get`,
`create`, `update`, `delete`, and `action` cover profiles, credentials,
connections, agents, runs and webhooks. Credentials are write-only; listing them
returns metadata. `events` supports a resumable integer cursor.

## Java and MCP

```java
try (var cloud = HostedClient.fromEnvironment()) {
    var agents = cloud.agents();
    var run = agents.run("Run tests", "PROFILE_ID");
    agents.follow(run.path("id").asText(), 0, java.time.Duration.ofHours(1),
        event -> System.out.println(event.path("text").asText()));
}
```

Java uses `VMNEST_API_TOKEN`, created with `vmnest token create --name java`.
The Java `Agents` methods mirror the Python operations, returning Jackson JSON.
Install the optional MCP support only when connecting an MCP client:

```sh
pip install -U "vmnest[mcp]"
```

The hosted MCP catalog includes `agent_run`, `agent_message`, `agent_run_events`,
`agent_run_cancel`, profile creation, durable agent controls, resource inspection,
and artifact listing. Connect provider credentials explicitly through the CLI
before giving an agent these tools.

Provider and connection credentials stay in the control service. The VM receives
a short-lived capability for its run. The service checks connection and tool
grants on each call; editable guest configuration is not the access boundary.
Codex native app integration uses the experimental app-server protocol and is a
pilot feature. Provider login and model access can expire independently of a
VMNest account.
