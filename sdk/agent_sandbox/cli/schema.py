"""Offline command discovery for automation clients."""

from enum import Enum
from typing import Literal, get_args, get_origin

import typer
from typer.core import TyperOption
from typer.main import get_command

from .mode_help import (
    DIRECT_OPTIONS,
    HOSTED_CREATE_OPTIONS,
    HOSTED_OPTIONS,
    selected_mode,
    unavailable,
)


def describe(app):
    mode = selected_mode()

    def walk(command, path):
        parameters = []
        for param in command.params:
            hidden = set()
            if len(path) == 1:
                if mode == "hosted":
                    hidden = DIRECT_OPTIONS | ({"image"} if path[0] == "create" else set())
                elif mode == "self-hosted":
                    hidden = HOSTED_OPTIONS | (
                        HOSTED_CREATE_OPTIONS if path[0] == "create" else set()
                    )
            if isinstance(param, TyperOption) and param.hidden:
                continue
            if param.name in hidden:
                continue
            kind = getattr(param, "type", None)
            annotation = getattr(getattr(param, "type_descriptor", None), "annotation", None)
            type_name = getattr(kind, "name", None) or getattr(
                annotation, "__name__", str(annotation)
            )
            choices = list(kind.choices) if hasattr(kind, "choices") else None
            if get_origin(annotation) is Literal:
                choices = list(get_args(annotation))
            elif isinstance(annotation, type) and issubclass(annotation, Enum):
                choices = [item.value for item in annotation]
            parameters.append(
                {
                    "name": param.name,
                    "kind": "option" if isinstance(param, TyperOption) else "argument",
                    "flags": param.opts if isinstance(param, TyperOption) else [],
                    "type": type_name,
                    "min": getattr(param, "min", getattr(kind, "min", None)),
                    "max": getattr(param, "max", getattr(kind, "max", None)),
                    "required": param.required,
                    "multiple": param.multiple or param.nargs == -1,
                    "default": param.default
                    if isinstance(param.default, (str, int, float, bool, list))
                    else None,
                    "choices": choices,
                }
            )
        result = {"path": path, "help": command.help, "parameters": parameters}
        if hasattr(command, "commands"):
            result["commands"] = [
                walk(child, path + [name])
                for name, child in sorted(command.commands.items())
                if path or not unavailable(name, mode)
            ]
        return result

    return {"schema_version": 1, "mode": mode, "cli": walk(get_command(app), [])}


def register(app):
    @app.command("schema")
    def schema():
        """Print the command schema as JSON without contacting the service."""
        import json

        typer.echo(json.dumps(describe(app)))
