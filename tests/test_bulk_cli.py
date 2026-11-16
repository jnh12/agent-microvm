import json
import threading
import time

import pytest
from agent_sandbox.cli.main import app
from agent_sandbox.errors import SandboxError
from typer.testing import CliRunner


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    calls = []

    def request(method, path):
        calls.append((method, path))
        if method == "GET":
            return {"items": [{"name": "one"}, {"name": "two"}]}
        if "/bad/" in path:
            raise SandboxError("Computer unavailable")
        return {"name": path.split("/")[3].split("?")[0], "status": "saving"}

    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    return calls


@pytest.mark.parametrize(
    "command,method,suffix",
    [
        ("wake", "POST", "/open"),
        ("shutdown", "POST", "/shutdown"),
        ("delete", "DELETE", "?permanent=true"),
    ],
)
def test_batch_routes_and_deduplicates(calls, command, method, suffix):
    r = CliRunner().invoke(app, [command, "one", "two", "one", "--json"])
    assert r.exit_code == 0, r.output
    result = json.loads(r.output)
    assert [i["name"] for i in result["items"]] == ["one", "two"]
    assert set(calls) == {(method, "/v1/vms/" + n + suffix) for n in ("one", "two")}


def test_failure_does_not_skip_other_targets(calls):
    r = CliRunner().invoke(app, ["shutdown", "one", "bad", "two", "--json"])
    assert r.exit_code == 1
    result = json.loads(r.output)
    assert result["succeeded"] == 2 and result["failed"] == 1
    assert result["items"][1]["error"] == "Computer unavailable"
    assert result["items"][0]["result"]["status"] == "saving"


def test_all_dry_run_only_lists(calls):
    r = CliRunner().invoke(app, ["delete", "--all", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["names"] == ["one", "two"]
    assert calls == [("GET", "/v1/vms")]


@pytest.mark.parametrize("args", [[], ["one", "--all"], ["one", "--parallel", "0"]])
def test_invalid_selection_does_not_mutate(calls, args):
    assert CliRunner().invoke(app, ["delete", *args]).exit_code == 2
    assert calls == []


def test_single_response_compatible(calls):
    r = CliRunner().invoke(app, ["shutdown", "one", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"name": "one", "status": "saving"}


@pytest.mark.parametrize("failure", [False, True])
def test_inventory_empty_or_unavailable(monkeypatch, calls, failure):
    def request(method, path):
        assert (method, path) == ("GET", "/v1/vms")
        if failure:
            raise SandboxError("Sign in first")
        return {"items": []}

    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    r = CliRunner().invoke(app, ["delete", "--all", "--json"])
    assert r.exit_code == int(failure)
    result = json.loads(r.output)
    assert result["items"] == []
    assert result["ok"] is not failure


def test_parallel_limit(monkeypatch, calls):
    active = peak = 0
    lock = threading.Lock()

    def request(method, path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        time.sleep(0.03)
        with lock:
            active -= 1
        return {"status": "saving"}

    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    r = CliRunner().invoke(app, ["shutdown", "a", "b", "c", "d", "--parallel", "2"])
    assert r.exit_code == 0, r.output
    assert peak == 2


def test_bulk_create_shared_options_and_retry_keys(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    calls = []

    def request(method, path, body=None):
        assert (method, path) == ("POST", "/v1/vms")
        calls.append(body)
        if body["name"] == "bad":
            raise SandboxError("Quota reached")
        return {"name": body["name"], "status": "preparing"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    options = [
        "--agent",
        "codex",
        "--prompt",
        "hello",
        "--disposable",
        "--request-key",
        "batch-one",
        "--json",
    ]
    runner = CliRunner()
    result = runner.invoke(app, ["create", "one", "bad", "one", *options])
    assert result.exit_code == 1, result.output
    summary = json.loads(result.output)
    assert summary["succeeded"] == 1 and summary["failed"] == 1
    assert len(calls) == 2
    keys = {b["name"]: b["request_key"] for b in calls}
    assert len(set(keys.values())) == 2
    assert all(b["prompt"] == "hello" and b["disposable"] and not b["wait"] for b in calls)
    calls.clear()
    runner.invoke(app, ["create", "bad", "one", *options])
    assert {b["name"]: b["request_key"] for b in calls} == keys
    calls.clear()
    single = runner.invoke(
        app,
        [
            "create",
            "one",
            "--agent",
            "codex",
            "--prompt",
            "hello",
            "--disposable",
            "--request-key",
            keys["one"],
            "--json",
        ],
    )
    assert single.exit_code == 0, single.output
    assert calls[0]["request_key"] == keys["one"]


def test_bulk_create_invalid_name_no_mutations(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")

    def request(*args, **kwargs):
        pytest.fail("Invalid batch must not create any computers")

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    result = CliRunner().invoke(app, ["create", "valid", "bad/name", "--agent", "codex"])
    assert result.exit_code == 2, result.output


@pytest.mark.parametrize("response", [None, [], "accepted", 42, True])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("command", ["create", "wake", "shutdown", "delete"])
def test_malformed_item_response_preserves_batch_outcomes(monkeypatch, command, as_json, response):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    monkeypatch.setenv("VMNEST_API_URL", "http://127.0.0.1:8767")
    calls = []

    def request(method, path, body=None):
        name = body["name"] if body else path.split("/")[3].split("?")[0]
        calls.append(name)
        if name == "bad":
            return response
        return {"name": name, "status": "preparing"}

    monkeypatch.setattr("agent_sandbox.cli.remote.request", request)
    monkeypatch.setattr("agent_sandbox.cli.computers.request", request)
    args = [command, "one", "bad", "two", "--parallel", "1"]
    if command == "create":
        args += ["--agent", "codex", "--request-key", "batch-one"]
    if as_json:
        args.append("--json")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1, result.output
    assert calls == ["one", "bad", "two"]
    error = "Invalid backend response; operation may have completed."
    if as_json:
        summary = json.loads(result.output)
        assert summary["ok"] is False
        assert summary["succeeded"] == 2
        assert summary["failed"] == 1
        assert [item["name"] for item in summary["items"]] == ["one", "bad", "two"]
        assert [item["ok"] for item in summary["items"]] == [True, False, True]
        assert summary["items"][1]["error"] == error
        if command == "create":
            assert summary["request_key"] == "batch-one"
            assert summary["items"][1]["request_key"].startswith("batch-")
    else:
        assert f"bad: {error}" in result.output
        verb = "accepted" if command == "create" else "succeeded"
        assert f"2 {verb}; 1 failed." in result.output


@pytest.mark.parametrize("command", ["wake", "shutdown", "delete"])
def test_single_malformed_response_reports_json_failure(monkeypatch, calls, command):
    monkeypatch.setattr("agent_sandbox.cli.computers.request", lambda *args: None)
    result = CliRunner().invoke(app, [command, "one", "--json"])
    assert result.exit_code == 1, result.output
    summary = json.loads(result.output)
    assert summary["ok"] is False
    assert summary["succeeded"] == 0 and summary["failed"] == 1
    assert summary["items"] == [
        {
            "name": "one",
            "ok": False,
            "error": "Invalid backend response; operation may have completed.",
        }
    ]
