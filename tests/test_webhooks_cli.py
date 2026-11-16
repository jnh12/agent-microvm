import json

import pytest
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


def test_update_and_test_delivery(calls, monkeypatch, tmp_path):
    result = CliRunner().invoke(
        app,
        ["webhook", "update", "tickets", "--profile", "support", "--concurrency", "2", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1] == ("PATCH", "/v1/webhooks/h1", {"profile": "support", "concurrency": 2})
    seen = []

    def trigger(self, method, path, **kwargs):
        seen.append((method, path, kwargs))
        return {"id": "job", "status": "queued"}

    monkeypatch.setattr("agent_sandbox.accounts.AccountClient.request", trigger)
    payload = tmp_path / "input.json"
    payload.write_text('{"ticket":123}', encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "webhook",
            "test",
            "tickets",
            "--secret-file",
            "-",
            "--request-key",
            "test-1",
            "--prompt",
            "Check ticket",
            "--input-file",
            str(payload),
            "--json",
        ],
        input="private-secret\n",
    )
    assert result.exit_code == 0, result.output
    assert "private-secret" not in result.output
    assert seen == [
        (
            "POST",
            "/v1/webhooks/h1/trigger",
            {
                "token": "private-secret",
                "body": {"prompt": "Check ticket", "input": {"ticket": 123}},
                "request_key": "test-1",
            },
        )
    ]


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr("agent_sandbox.cli.webhooks.service_url", lambda: "https://api.test")
    seen = []

    def request(method, path, body=None):
        seen.append((method, path, body))
        if method == "GET" and path == "/v1/webhooks":
            return {"items": [{"id": "h1", "name": "tickets", "enabled": True}]}
        return {"id": "hook1", "trigger_path": "/v1/webhooks/hook1/trigger", "secret": "once"}

    monkeypatch.setattr("agent_sandbox.cli.webhooks.api", request)
    return seen


def test_create_prints_single_json_document(calls):
    result = CliRunner().invoke(
        app, ["webhook", "create", "tickets", "--profile", "support", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        ("POST", "/v1/webhooks", {"name": "tickets", "profile": "support", "concurrency": 1})
    ]
    assert json.loads(result.output)["trigger_url"] == "https://api.test/v1/webhooks/hook1/trigger"


@pytest.mark.parametrize(
    "args,method,path",
    [
        (["list"], "GET", "/v1/webhooks"),
        (["revoke", "h1"], "DELETE", "/v1/webhooks/h1"),
        (["rotate", "h1"], "POST", "/v1/webhooks/h1/rotate"),
        (["runs", "h1", "--limit", "4"], "GET", "/v1/webhooks/h1/runs?limit=4"),
        (["inspect", "j1"], "GET", "/v1/webhook-runs/j1"),
        (["logs", "j1", "--after", "7"], "GET", "/v1/webhook-runs/j1/events?after=7"),
        (["cancel", "j1"], "POST", "/v1/webhook-runs/j1/cancel"),
    ],
)
def test_webhook_commands(calls, args, method, path):
    result = CliRunner().invoke(app, ["webhook", *args, "--json"])
    assert result.exit_code == 0, result.output
    json.loads(result.output)
    prefix = [("GET", "/v1/webhooks", None)] if args[0] in ("rotate", "revoke", "runs") else []
    assert calls == prefix + [(method, path, None)]


def test_webhooks_are_hosted_only(calls, monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    result = CliRunner().invoke(app, ["webhook", "list", "--json"])
    assert result.exit_code != 0 and not calls


def test_profile_workflow_update_does_not_prompt(monkeypatch, tmp_path):
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        return {"name": "support", "provider": "codex", "integrations": [], **(body or {})}

    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", request)
    instructions = tmp_path / "instructions.txt"
    instructions.write_text("Fix tickets.\nOpen a PR.", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "profile",
            "update",
            "support",
            "--instructions-file",
            str(instructions),
            "--timeout",
            "900",
            "--setup",
            "echo ready",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][2] == {
        "instructions": "Fix tickets.\nOpen a PR.",
        "setup_command": "echo ready",
        "timeout_seconds": 900,
    }
    assert len(calls) == 2


def test_profile_rejects_conflicting_instructions_before_network(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr("agent_sandbox.cli.agent_profiles.api", lambda *a: pytest.fail("network"))
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "profile",
            "create",
            "support",
            "--instructions",
            "test",
            "--instructions-file",
            "test.txt",
            "--no-integrations",
            "--json",
        ],
    )
    assert result.exit_code == 1 and json.loads(result.output)["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("command", ["rotate", "revoke", "runs"])
def test_webhook_names_resolve(calls, command):
    result = CliRunner().invoke(app, ["webhook", command, "tickets", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1][1].startswith("/v1/webhooks/h1")


def test_revoked_name_cannot_rotate(monkeypatch):
    seen = []

    def request(method, path, body=None):
        seen.append(method)
        return {"items": [{"id": "h1", "name": "test", "enabled": False}]}

    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setattr("agent_sandbox.cli.webhooks.api", request)
    result = CliRunner().invoke(app, ["webhook", "rotate", "test", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "WEBHOOK_REVOKED"
    assert seen == ["GET"]


def test_unknown_webhook_name(calls):
    result = CliRunner().invoke(app, ["webhook", "rotate", "missing", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "WEBHOOK_NOT_FOUND"
    assert calls == [("GET", "/v1/webhooks", None)]
