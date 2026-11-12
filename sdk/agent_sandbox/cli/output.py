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
