"""Provider-reported model choices, effort levels, and account defaults."""

from typing import Literal
from urllib.parse import quote
import typer
from agent_sandbox.accounts import AccountError
from .accounts import handled
from .agent_profiles import api
from .output import table, details


def provider_name(provider):
    if provider:
        return provider
    from .agent_defaults import resolve_agent

    provider = resolve_agent(None, api)
    if not provider:
        raise AccountError("PROVIDER_REQUIRED", "Choose --provider codex or --provider claude.")
    return provider


def show(catalog):
    table(
        "Available models",
        ["Model", "Name", "Effort levels"],
        (
            (m["id"], m["name"], ", ".join(m["efforts"]) or "Provider default only")
            for m in catalog["items"]
        ),
    )


def choose(
    provider, model=None, effort=None, *, interactive=False, as_json=False, current_model=None
):
    if model is None and effort is None and not interactive:
        return model, effort
    catalog = api("GET", f"/v1/agent-connections/{provider}/models")
    if not as_json:
        show(catalog)
    if interactive or model == "select":
        if as_json:
            raise AccountError(
                "MODEL_REQUIRED", "Specify --model; use agent models --json to list choices."
            )
        model = typer.prompt(
            "Model (or default)", default=catalog["defaults"].get("model") or "default"
        )
    effective = model or current_model or catalog["defaults"].get("model") or "default"
    chosen = next(
        (
            m
            for m in catalog["items"]
            if m["id"] == effective or m.get("resolved_model") == effective
        ),
        None,
    )
    if effective == "default":
        chosen = next((m for m in catalog["items"] if m["default"]), None)
    if not chosen and effective != "default":
        raise AccountError(
            "MODEL_UNAVAILABLE",
            "Available models: " + ", ".join(m["id"] for m in catalog["items"]) + "; default",
        )
    levels = chosen["efforts"] if chosen else []
    if not as_json:
        typer.echo("Available effort levels: " + ", ".join(["default", *levels]))
    if interactive or effort == "select":
        if as_json:
            raise AccountError(
                "EFFORT_REQUIRED", "Specify --effort; use agent models --json to list choices."
            )
        effort = typer.prompt("Effort", default="default") if levels else "default"
    if effort and effort != "default" and effort not in levels:
        raise AccountError(
            "EFFORT_UNAVAILABLE",
            "Available effort levels for " + effective + ": " + ", ".join(["default", *levels]),
        )
    return model, effort


def for_computer(name, model, effort, session="main", as_json=False):
    if model is None and effort is None:
        return model, effort
    vm = api("GET", "/v1/vms/" + quote(name, safe=""))
    agent = vm.get("agent") or {}
    if not agent.get("provider"):
        raise AccountError("AGENT_REQUIRED", "This computer has no agent.")
    current = agent.get("model")
    if model is None:
        runs = api(
            "GET",
            "/v1/vms/" + quote(name, safe="") + "/agent/runs?session=" + quote(session, safe=""),
        )["items"]
        if runs:
            current = runs[0].get("model") or "default"
    return choose(agent["provider"], model, effort, as_json=as_json, current_model=current)


def register(app):
    @app.command("models")
    @handled
    def models(
        provider: Literal["codex", "claude"] | None = typer.Option(None, "--provider"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        result = api("GET", f"/v1/agent-connections/{provider_name(provider)}/models")
        if as_json:
            details(result, True)
        else:
            show(result)
            typer.echo(
                "Saved default: "
                + (result["defaults"].get("model") or "provider default")
                + "; effort: "
                + (result["defaults"].get("effort") or "default")
            )

    @app.command("model")
    @handled
    def model_default(
        model: str | None = typer.Argument(None),
        provider: Literal["codex", "claude"] | None = typer.Option(None, "--provider"),
        effort: str | None = typer.Option(None, "--effort"),
        as_json: bool = typer.Option(False, "--json"),
    ):
        """Choose the default model and effort for new conversations."""
        provider = provider_name(provider)
        model, effort = choose(provider, model, effort, interactive=model is None, as_json=as_json)
        result = api(
            "PUT",
            f"/v1/agent-connections/{provider}/model-defaults",
            {"model": model, "effort": effort or "default"},
        )
        details(result, as_json, "Default model")
