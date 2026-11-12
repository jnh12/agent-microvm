import json
import re

import pytest
from agent_sandbox.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def help_text(monkeypatch, selected, *args):
    monkeypatch.setenv("VMNEST_MODE", selected)
    result = runner.invoke(app, [*args, "--help"], color=False)
    assert result.exit_code == 0, result.output
    return result.output


def test_root_switches_without_mutation(monkeypatch):
    for selected in ("hosted", "self-hosted", "hosted"):
        text = help_text(monkeypatch, selected)
        assert "create" in text and "upload" in text and "setup" in text
        for name in ("agent", "project", "usage", "open", "login"):
            assert (re.search(r"\u2502\s+" + re.escape(name) + r"\s{2,}", text) is not None) == (
                selected == "hosted"
            )
        for name in ("infra", "image", "mcp"):
            assert (re.search(r"\u2502\s+" + re.escape(name) + r"\s{2,}", text) is not None) == (
                selected == "self-hosted"
            )


@pytest.mark.parametrize("selected", ["hosted", "self-hosted"])
def test_options(monkeypatch, selected):
    text = help_text(monkeypatch, selected, "create")
    hosted = [
        "--agent",
        "--repo",
        "--setup",
        "--profile",
        "--prompt",
        "--approvals",
        "--disposable",
        "--json",
        "--request-key",
        "--idle-minutes",
    ]
    direct = ["--region", "--role", "--image", "--cpus", "--memory", "--egress"]
    for flag in hosted:
        assert (flag in text) == (selected == "hosted")
    for flag in direct:
        assert (flag in text) == (selected == "self-hosted")
    text = help_text(monkeypatch, selected, "exec")
    assert ("--region" in text) == (selected == "self-hosted")
    assert ("--request-key" in text) == (selected == "hosted")
    text = help_text(monkeypatch, selected, "forward")
    assert "--remote-port" in text and "--local-port" in text
    assert ("--no-verify-tls" in text) == (selected == "self-hosted")


def test_saved_mode_and_offline_help(monkeypatch):
    from agent_sandbox.accounts import config_path

    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.delenv("VMNEST_API_URL", raising=False)
    config_path().write_text(json.dumps({"mode": "hosted"}))

    def forbidden(*args, **kwargs):
        pytest.fail("Help must not use transport or credentials")

    monkeypatch.setattr("agent_sandbox.remote.request", forbidden)
    monkeypatch.setattr("agent_sandbox.accounts.secret", forbidden)
    for args in ([], ["create"], ["agent", "send"], ["project", "logs"]):
        result = runner.invoke(app, [*args, "--help"])
        assert result.exit_code == 0, result.output
    config_path().unlink()
    result = runner.invoke(app, ["--help"])
    assert "Choose a mode" in result.output
    assert " infra " in result.output and " agent " in result.output


@pytest.mark.parametrize(
    "selected,command",
    [("hosted", "infra"), ("self-hosted", "agent"), ("self-hosted", "open")],
)
def test_mode_errors(monkeypatch, selected, command):
    monkeypatch.setenv("VMNEST_MODE", selected)
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code != 0
    assert "vmnest setup --mode" in result.output


def test_environment_override(monkeypatch):
    monkeypatch.delenv("VMNEST_MODE", raising=False)
    monkeypatch.setenv("VMNEST_API_URL", "https://example.invalid")
    assert " infra " not in runner.invoke(app, ["--help"]).output
    assert " infra " in help_text(monkeypatch, "self-hosted")
    assert "default" in help_text(monkeypatch, "hosted", "run")


def test_explicit_state_management_removed(monkeypatch):
    text = help_text(monkeypatch, "hosted")
    for command in (
        "checkpoint",
        "checkpoint-status",
        "restore",
        "workspaces",
        "start",
        "stop",
        "rm",
    ):
        assert re.search(r"\u2502\s+" + re.escape(command) + r"\s{2,}", text) is None
    text = help_text(monkeypatch, "hosted", "agent")
    for command in ("checkpoint", "checkpoint-status", "restore", "workspaces"):
        assert command not in text
