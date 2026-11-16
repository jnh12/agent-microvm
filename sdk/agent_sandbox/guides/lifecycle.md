# Computer lifecycle

Use ls/inspect for authoritative state, wait for readiness, and usage for quota/billing. A computer's power state, setup state, and agent run state are distinct. Failed setup is not the same as a failed shell command. Do not infer that a VM is off from a task failure; inspect it. Saving/stopping are transitional states, not permission to recreate the computer.

Persistent computers preserve saved workspace data between shutdown and wake. Disposable computers discard their workspace on shutdown and cannot wake. Create with --disposable only when artifacts will be exported or published before shutdown. `run` executes a one-off shell command in an ephemeral sandbox; consult its help for command separator and image argument.

Use configure to inspect/change settings. Changes to running configuration may require save and restart. Respect the API restart contract and surface any save failure rather than claiming persistence. Renaming changes the display label, not the stable CLI identifier. Shutdown can interrupt work; deletion removes data permanently. Batch wake/shutdown/delete accept names, --parallel, and --dry-run; --all has account-wide impact.

File read/write and command execution can wake a computer and incur usage. Upload/download operate on explicit paths. Hosted MCP transfers base64 content, not files on the caller's laptop. Port access can return sensitive credentials; never publish them. Prefer short bounded commands and inspect exit status, stdout and stderr separately.
