# Account cloud defaults

`vmnest defaults` uses the same signed-in account settings as the website's
**Settings → Cloud sandbox defaults**. Changes apply to future computers,
including webhook and ephemeral computers. Existing computers retain their
saved settings when waking. These commands require hosted mode and login.

```sh
vmnest defaults set API_KEY
vmnest defaults list
vmnest defaults remove API_KEY
vmnest defaults script --file setup.sh
vmnest defaults script
vmnest defaults script --clear
```

`set` without input flags asks for a hidden value in an interactive terminal.
For agents and scripts, use stdin or a UTF-8 file, avoiding secrets in command
arguments and shell history:

```sh
vmnest defaults set API_KEY --stdin --json < secret.txt
vmnest defaults set API_KEY --file secret.txt --json
vmnest defaults script --file - --json < setup.sh
vmnest defaults list --json
```

PowerShell can pipe a secret obtained from a secret manager or existing variable:

```powershell
$env:SERVICE_API_KEY | vmnest defaults set SERVICE_API_KEY --stdin --json
vmnest defaults script --file .\setup.sh --json
```

`--stdin` removes one trailing line ending; internal newlines and other whitespace
are preserved. For variable values, `--file` preserves the exact UTF-8 text.
Script uploads normalize Windows line endings for the Linux shell. An empty value is valid.
Values are limited to 4,096 characters; scripts to 32,000. The service also
enforces valid names, reserved runtime names, at most 64 variables, and a total
64 KiB settings limit.

`list` returns names, revision, and whether a script is configured. It never
returns saved variable values or script contents. `script` without flags is an
explicit request to read the saved script. Store credentials in variable fields,
not inside the script. All commands accept `--json` and return nonzero on failure.

Each update reads the latest revision, then changes only the selected variable
or script. Concurrent edits fail with `DEFAULTS_CHANGED` rather than silently
overwriting them; review the defaults before retrying. An uncertain network
failure is not automatically replayed. Successful writes immediately appear in
website Settings when reloaded; saved values remain hidden there.
