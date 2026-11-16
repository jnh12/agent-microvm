# Defaults, secrets and configuration

Account defaults apply to new cloud computers. `vmnest defaults list --json` shows keys/metadata, not secret values. Set a key with `vmnest defaults set NAME --file value.txt --json` or --stdin. `vmnest defaults script --file startup.sh --json` sets the startup script; --clear removes it. Protect input files and do not echo secrets in shell history, logs, prompts or source control.

Profiles can set their own environment keys and startup script with --env-file and --setup-file; use profile set-key for a single secret and update help for removal/clear options. Repository setup must use a public repository. Scripts run on cloud computers, not on the agent's host; make setup repeatable because restores/restarts can run setup again. Inspect selected profile and account defaults before launching. Do not assume updated defaults are injected into an already-created machine.

Use computer configuration for idle timer, agent model/effort and integrations; honor restart requirements. Endpoint selections control outbound notifications. Updates using revisions must read the latest revision and reapply the intended change on conflict, not overwrite unrelated settings. Never imply secret values can be retrieved after storage.

The website and hosted CLI/MCP share account settings through the API. An account connection is not the same as permissions inside an external service. Local AWS/self-hosted configuration is a separate mode and is not needed for hosted use.
