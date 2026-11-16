# Errors and recovery

Read structured error.code and message, process exit status and any run_id/request_key. CLI --json is command-specific; do not assume every command emits the same envelope. Preserve exact diagnostics while removing secrets before sharing.

Authentication failure: verify whoami and sign in again if needed. Do not retry a bad token indefinitely. AGENT_RECONNECT_REQUIRED: reconnect the correct provider with --replace after authorized credential discovery; inspect connection/integration state. CONNECTION_EXISTS: inspect the existing connection and replace only deliberately.

Timeout/service unavailable/unknown outcome: inspect the named computer or recorded run before retrying. Reuse the original request key when supported. Back off, bound retries, and report unresolved outcome instead of claiming failure or completion. Rate/quota/capacity errors: inspect usage and do not create alternate machines to bypass limits.

Setup failure: inspect configuration/project logs and fix the cause before retrying setup. Do not repeatedly execute a failing startup script. Save failure: the VM may still shut down; warn that the latest files may not be preserved and inspect final state. Failed command: inspect command exit code and output; this is not proof of VM failure.

No integrations: inspect provider connection and computer settings, then apply the documented restart flow. No events: inspect subscription, attached endpoints, delivery history and receiver response. Webhook accepted: follow its run ID; acceptance does not guarantee task success. Disposed machine: create a new one only within the user's requested scope, never wake it.
