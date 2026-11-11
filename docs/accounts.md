# Accounts

Choose hosted login or self-hosted AWS. Hosted mode routes VM lifecycle commands
through the VMNest service using your account. Customers do not need AWS credentials.

```sh
vmnest setup --mode hosted --url https://YOUR-ACCOUNT-SERVICE
vmnest login
vmnest whoami
vmnest create my-vm
vmnest ls
vmnest inspect my-vm
vmnest rm my-vm
```

`login` opens GitHub in your browser. Approve VMNest and return to the terminal.
First login creates your account; your session stays valid until logout or revocation.
Use `--device` for code-based login, or `--no-browser` to open the approval link yourself.

For your own AWS account, no VMNest login is required:

```sh
vmnest setup --mode self-hosted
vmnest infra init
```

Setup saves the choice but provisions nothing. Existing direct-AWS commands still
work without a saved mode. Hosted mode currently supports `create`, `ls`, `inspect`,
and `rm`. Guest execution, transfers, forwarding, infrastructure, images, and MCP
remain unavailable in hosted mode. It never falls back to local AWS credentials.
The Python SDK remains the direct-AWS SDK. Switching modes does not log you out.

## Commands

| Command | Purpose |
| --- | --- |
| `setup` | Select a mode; interactive if no mode is supplied |
| `login` | Browser login (`--device` for fallback, `--resume` to continue) |
| `whoami` | Show your mode and verify your hosted account |
| `logout` | Revoke the current session and remove the saved credential |

All account commands support `--json`. Plain text is intended for people; JSON is
stable output for automation. Running `vmnest` alone explains the two routes.

## Agents

```sh
vmnest setup --mode hosted --url https://YOUR-ACCOUNT-SERVICE --json
vmnest login --json
# Open verification_uri in the user's browser; wait at least interval seconds.
vmnest login --resume --json
vmnest whoami --json
```

JSON login starts or polls once and exits. A pending response includes
`status: pending`, `code: USER_APPROVAL_REQUIRED`, `verification_uri`, `interval`,
`expires_at`, and `next_command`. Device mode also includes `user_code`.
Repeat `--resume` until authenticated or expired; do not start a new login each time.
Open the approval link in the user's browser, not an agent HTTP client. It is one-use.

Success and pending responses exit 0 with `ok: true`. Operational failures exit 1
with `ok: false` and `error.code`/`error.message`; syntax errors exit 2.
Common codes include `MODE_REQUIRED`, `MODE_CONFLICT`, `AUTH_REQUIRED`, `AUTH_EXPIRED`
(invalid/revoked credentials), `LOGIN_EXPIRED`, and `SERVICE_UNAVAILABLE`.

Agents under the same OS user can use the saved session. Separate unattended API keys
and `VMNEST_TOKEN` support are not implemented. Human GitHub approval is still required.

## Hosted VM lifecycle

```sh
vmnest create task-vm --request-key job-123 --json
vmnest inspect task-vm --json
vmnest ls --json
vmnest rm task-vm --json
```

JSON lifecycle commands return immediately with a VM record, or `{ "items": [...] }`
for `ls`. Poll `inspect` until `RUNNING` before using a VM, or `TERMINATED` after removal.
Human create/remove commands wait up to 30 seconds by default. Set the client
environment variable `VMNEST_WAIT_SECONDS` to a nonnegative integer to change this;
zero skips waiting. VM records contain `id`, `name`,
`state`, `region`, Unix-second timestamps `created_at` and `expires_at`, and `error`.
Unlike account responses, successful lifecycle records have no `ok` envelope.
Errors use the same `ok: false` envelope and exit code 1 as account commands.

Reuse a request key after a timeout. The CLI generates one if omitted and returns it
in create JSON. Keys belong to your account; reusing a key with another name fails.
A replay returns the original VM even after termination. Use a new key for a new VM.
Creating an already-active name returns that VM. Names and IDs are accepted by
`inspect` and `rm`; lists include terminated history and only your own VMs.

The default hosted configuration allows one active VM per account, in Frankfurt,
using the service's fixed image and runtime role, with a one-hour maximum lifetime.
AWS enforces that lifetime even if the API stops. AWS options such as `--role`,
`--image`, `--region`, `--cpus`, `--memory`, and egress settings are service-managed.
`UNKNOWN` with `LAUNCH_UNCERTAIN` requires operator reconciliation and continues
to occupy the account's slot. Retrying never intentionally creates a replacement.
These limits apply to VMs; account sessions still have no automatic expiry.
The service operator can configure quotas, lifetime, idle behavior, and recovery
timings through the [service environment settings](../../vmnest-accounts/README.md).

## Configuration

- Non-secret settings: `~/.vmnest/config.json`, overridden by `VMNEST_CONFIG`.
- Environment overrides: `VMNEST_MODE`, `VMNEST_URL`. Unset conflicting mode overrides
  before changing modes. URLs require HTTPS except for loopback development.
- Sessions and pending tickets: Windows Credential Manager, macOS Keychain, or Linux
  Secret Service/KWallet, scoped to the service URL. No plaintext fallback.
- Sessions have `expires_at: null`; unfinished logins still expire.
- Logout retains the credential if server revocation fails, so it can be retried.
- Session tokens and poll tickets never appear in normal output.

Headless systems need an accessible OS credential store. The public service URL is
not configured yet; local testing uses `http://127.0.0.1:8766`.
