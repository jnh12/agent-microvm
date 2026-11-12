"""Human-readable terminal output, with explicit JSON for automation."""

import json

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)


def label(value):
    return str(value).replace("_", " ").strip().capitalize()


def value_text(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(value_text(item) for item in value) or "None"
    if isinstance(value, dict):
        return "; ".join(f"{label(k)}: {value_text(v)}" for k, v in value.items()) or "None"
    return str(value)


def table(title, columns, rows, empty="Nothing to show."):
    rows = list(rows)
    if not rows:
        console.print(empty, markup=False)
        return
    view = Table(title=title, header_style="bold cyan", expand=False)
    for column in columns:
        view.add_column(column, overflow="fold")
    for row in rows:
        view.add_row(*(Text(value_text(value)) for value in row))
    console.print(view)


def details(result, as_json=False, title=None):
    if as_json:
        typer.echo(json.dumps(result, default=str))
    elif isinstance(result, dict):
        table(title, ["Field", "Value"], ((label(k), v) for k, v in result.items()))
    elif isinstance(result, list):
        for item in result:
            details(item, title=title)
        if not result:
            console.print("Nothing to show.")
    else:
        console.print(value_text(result), markup=False)


def workspaces(result, as_json=False):
    if as_json:
        typer.echo(json.dumps(result))
        return
    table(
        "Workspaces",
        ["Name", "Saved checkpoint", "Latest operation"],
        (
            (
                row["name"],
                "Available" if row.get("checkpoint") else "Not yet saved",
                (
                    (row.get("operation") or {}).get("kind", "")
                    + " "
                    + (row.get("operation") or {}).get("phase", "")
                ).strip()
                or "—",
            )
            for row in result.get("items", [])
        ),
        empty="No saved workspaces yet. Create a VM to get started.",
    )
    for row in result.get("items", []):
        error = (row.get("operation") or {}).get("error")
        if error:
            console.print(f"{row['name']}: {error}", style="red", markup=False)


def operation(result, as_json=False):
    if as_json:
        typer.echo(json.dumps(result))
        return
    console.print(
        f"{label(result.get('kind', 'Operation'))}: {label(result['phase'])}  ·  {result['id']}",
        markup=False,
    )
    if result.get("request_key"):
        console.print("Retry key: " + result["request_key"], style="dim", markup=False)
    if result.get("error"):
        console.print(result["error"], style="red", markup=False)
