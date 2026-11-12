"""Local mode-aware help; shared commands retain one implementation."""

from copy import copy

import typer
from typer.core import TyperCommand, TyperGroup

from agent_sandbox.accounts import AccountError, mode

HOSTED = {
    "login",
    "logout",
    "whoami",
    "usage",
    "agent",
    "project",
    "checkpoint",
    "checkpoint-status",
    "restore",
    "workspaces",
}
DIRECT = {"infra", "image", "mcp"}
DIRECT_OPTIONS = {
    "region",
    "role",
    "cpus",
    "memory",
    "egress",
    "egress_connector",
    "log_group",
    "no_verify_tls",
}
HOSTED_OPTIONS = {"request_key"}
HOSTED_CREATE_OPTIONS = {
    "agent",
    "profile",
    "prompt",
    "prompt_file",
    "approvals",
    "disposable",
    "repository",
    "setup_command",
    "as_json",
}


def selected_mode():
    try:
        return mode()
    except AccountError as exc:
        raise typer.BadParameter(str(exc)) from None


def unavailable(command, selected):
    return (selected == "hosted" and command in DIRECT) or (
        selected == "self-hosted" and command in HOSTED
    )


class ModeGroup(TyperGroup):
    def format_help(self, ctx, formatter):
        selected = selected_mode()
        view = copy(self)
        view.commands = {
            name: command
            for name, command in self.commands.items()
            if not unavailable(name, selected)
        }
        hint = (
            f"Mode: {selected}. Change with vmnest setup --mode hosted|self-hosted."
            if selected
            else "Choose a mode: vmnest setup --mode hosted or vmnest setup --mode self-hosted."
        )
        view.help = (self.help or "") + "\n\n" + hint
        TyperGroup.format_help(view, ctx, formatter)


class ModeCommand(TyperCommand):
    def format_help(self, ctx, formatter):
        selected = selected_mode()
        hidden = set()
        if selected == "hosted":
            hidden = DIRECT_OPTIONS | ({"image"} if self.name == "create" else set())
        elif selected == "self-hosted":
            hidden = HOSTED_OPTIONS | (HOSTED_CREATE_OPTIONS if self.name == "create" else set())
        view = copy(self)
        view.params = [copy(param) for param in self.params]
        for param in view.params:
            if param.name in hidden:
                param.hidden = True
            if selected == "hosted" and self.name == "run" and param.name == "image":
                param.help = "Use 'default' for the hosted backend image."
        TyperCommand.format_help(view, ctx, formatter)


class ModeApp(typer.Typer):
    def command(self, *args, **kwargs):
        kwargs.setdefault("cls", ModeCommand)
        return super().command(*args, **kwargs)
