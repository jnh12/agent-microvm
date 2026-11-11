# Accounts

Choose hosted login or self-hosted AWS. Hosted VM operations are not available yet;
account commands work against a separately configured accounts service.

```sh
vmnest setup --mode hosted --url https://YOUR-ACCOUNT-SERVICE
vmnest login
vmnest whoami
```

`login` opens GitHub in your browser. Approve VMNest and return to the terminal.
First login creates your account; your session stays valid until logout or revocation.
Use `--device` for code-based login, or `--no-browser` to open the approval link yourself.

For your own AWS account, no VMNest login is required:

```sh
vmnest setup --mode self-hosted
vmnest doctor
vmnest infra init
```

Setup saves the choice but provisions nothing. Existing direct-AWS commands still
work without a saved mode. Hosted mode blocks VM, infrastructure, image, and MCP
operations until implemented; it never silently falls back to AWS credentials.
The Python SDK remains the direct-AWS SDK. Switching modes does not log you out.

## Commands

| Command | Purpose |
| --- | --- |
| `setup` | Select a mode; interactive if no mode is supplied |
| `login` | Browser login (`--device` for fallback, `--resume` to continue) |
| `whoami` | Verify and display your account |
| `auth status` | Show mode and authentication status |
| `logout` | Revoke the current session and remove the saved credential |
| `doctor` | Check account service/login or self-hosted AWS identity |

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
