"""Account-wide cloud defaults shared with website Settings."""

import re
import sys
from pathlib import Path

import typer

from agent_sandbox.accounts import AccountError

from .accounts import handled
from .agent_models import interactive_terminal
from .agent_profiles import api
from .output import details, table

app = typer.Typer(
    name="defaults",
    help="Manage cloud defaults shared with website Settings.",
    no_args_is_help=True,
)
ROUTE = "/v1/cloud-defaults"


def read_input(filename, limit):
    try:
        if filename == "-":
            value = sys.stdin.read(limit + 1)
        else:
            with Path(filename).open(encoding="utf-8", newline="") as stream:
                value = stream.read(limit + 1)
    except (OSError, UnicodeError):
        raise AccountError("INVALID_REQUEST", "Could not read UTF-8 input.") from None
    if len(value) > limit or "\x00" in value:
        raise AccountError(
            "INVALID_REQUEST", f"Input must be at most {limit} characters without NUL bytes."
        )
    return value


def change(body):
    saved = api("GET", ROUTE)
    return api("PATCH", ROUTE, {"revision": saved["revision"], **body})


def valid_name(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
        raise AccountError("INVALID_REQUEST", "Use an environment variable name, such as API_KEY.")


@app.command("set")
@handled
def set_variable(
    name: str,
    stdin: bool = typer.Option(
        False, "--stdin", help="Read the value from stdin; remove one trailing newline."
    ),
    file: str | None = typer.Option(None, "--file", help="Read an exact UTF-8 value from a file."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Add or replace a variable. Without input flags, prompt secretly in a terminal."""
    valid_name(name)
    if stdin and file is not None:
        raise AccountError("INVALID_REQUEST", "Choose --stdin or --file.")
    if stdin:
        value = read_input("-", 4098)
        value = value.removesuffix("\n").removesuffix("\r")
    elif file is not None:
        value = read_input(file, 4096)
    else:
        if as_json or not interactive_terminal():
            raise AccountError("INPUT_REQUIRED", "Supply --stdin or --file for unattended use.")
        value = typer.prompt("Value", hide_input=True, default="", show_default=False)
    if len(value) > 4096 or "\x00" in value:
        raise AccountError(
            "INVALID_REQUEST", "Value must be at most 4096 characters without NUL bytes."
        )
    result = change({"variables": {name: value}})
    details({"name": name, "status": "saved", "revision": result["revision"]}, as_json)


@app.command("list")
@handled
def listing(as_json: bool = typer.Option(False, "--json")):
    """List variable names and whether a startup script is configured. Values stay hidden."""
    saved = api("GET", ROUTE)
    result = {
        "variables": saved["variables"],
        "revision": saved["revision"],
        "startup_script_configured": bool(saved["startup_script"]),
    }
    if as_json:
        details(result, True)
    else:
        table(
            "Cloud defaults",
            ["Variable"],
            ((name,) for name in result["variables"]),
            empty="No default environment variables.",
        )
        typer.echo(
            "Startup script: " + ("configured" if result["startup_script_configured"] else "none")
        )


@app.command("remove")
@handled
def remove(name: str, as_json: bool = typer.Option(False, "--json")):
    """Remove a variable from future computers."""
    valid_name(name)
    result = change({"variables": {name: None}})
    details({"name": name, "status": "removed", "revision": result["revision"]}, as_json)


@app.command("script")
@handled
def script(
    file: str | None = typer.Option(
        None, "--file", help="Upload a UTF-8 shell script; use - for stdin."
    ),
    clear: bool = typer.Option(False, "--clear", help="Remove the default startup script."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Show the startup script, upload one with --file, or remove it with --clear."""
    if file is not None and clear:
        raise AccountError("INVALID_REQUEST", "Choose --file or --clear.")
    if file is None and not clear:
        result = api("GET", ROUTE)
        if as_json:
            details(
                {"startup_script": result["startup_script"], "revision": result["revision"]}, True
            )
        else:
            typer.echo(result["startup_script"], nl=False)
        return
    value = "" if clear else read_input(file, 32000).replace("\r\n", "\n")
    result = change({"startup_script": value})
    details({"status": "cleared" if clear else "saved", "revision": result["revision"]}, as_json)
