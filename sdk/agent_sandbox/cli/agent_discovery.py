"""Read-only discovery of agent logins. Never print subprocess auth output."""

import getpass
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import keyring

from agent_sandbox.accounts import AccountError


def home(provider):
    key, folder = (
        ("CODEX_HOME", ".codex") if provider == "codex" else ("CLAUDE_CONFIG_DIR", ".claude")
    )
    return Path(os.environ.get(key, str(Path.home() / folder))).expanduser()


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=15, check=False)
        if result.returncode or len(result.stdout) > 65536:
            return None
        return result.stdout.decode("utf-8").strip()
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None


def keychain(provider):
    if provider == "codex":
        canonical = str(home(provider).resolve())
        if os.name == "nt" and not canonical.startswith("\\\\?\\"):
            canonical = "\\\\?\\" + canonical
        account = "cli|" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
        try:
            return keyring.get_password("Codex Auth", account)
        except keyring.errors.KeyringError:
            return None
    if sys.platform == "darwin":
        service = "Claude Code-credentials"
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            service += "-" + hashlib.sha256(str(home(provider)).encode()).hexdigest()[:8]
        return command(
            [
                "security",
                "find-generic-password",
                "-a",
                os.environ.get("USER") or getpass.getuser(),
                "-s",
                service,
                "-w",
            ]
        )
    return None


def wsl_distros():
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            ["wsl.exe", "--list", "--quiet"], capture_output=True, timeout=15, check=False
        )
        if result.returncode:
            return []
        encoding = "utf-16-le" if b"\x00" in result.stdout else "utf-8"
        return [
            name.strip().lstrip("\ufeff")
            for name in result.stdout.decode(encoding).splitlines()
            if name.strip()
        ]
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return []


def wsl_read(distro, provider):
    # Static Python program; distribution names and provider are separate argv.
    code = (
        "import os,pathlib,sys; p=sys.argv[1]; "
        "k,d,f=('CODEX_HOME','.codex','auth.json') if p=='codex' else "
        "('CLAUDE_CONFIG_DIR','.claude','.credentials.json'); "
        "path=pathlib.Path(os.environ.get(k,str(pathlib.Path.home()/d)))/f; "
        "assert path.stat().st_size<=65536; sys.stdout.write(path.read_text())"
    )
    return command(["wsl.exe", "--distribution", distro, "--exec", "python3", "-c", code, provider])


def discover(provider, clean):
    """Return source labels plus sanitized payloads; never persist a second local copy."""
    result = {}
    filename = "auth.json" if provider == "codex" else ".credentials.json"
    path = home(provider) / filename
    mode = "auto"
    if provider == "codex":
        try:
            mode = tomllib.loads((home(provider) / "config.toml").read_text())[
                "cli_auth_credentials_store"
            ]
        except (OSError, ValueError, KeyError):
            pass

    def add(label, raw):
        if raw and len(raw.encode()) <= 65536:
            try:
                result[label] = clean(provider, json.loads(raw))
            except (ValueError, TypeError, AccountError):
                pass

    if mode != "file":
        add("keychain", keychain(provider))
    # A configured keyring is authoritative; do not silently upload stale file fallback.
    if mode != "keyring" and "keychain" not in result:
        try:
            if path.stat().st_size <= 65536:
                add("local", path.read_text(encoding="utf-8"))
        except OSError:
            pass
    for distro in wsl_distros():
        add("wsl:" + distro, wsl_read(distro, provider))
    return result
