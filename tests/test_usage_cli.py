import json
from urllib.parse import parse_qs, urlsplit

from agent_sandbox.cli import usage
from agent_sandbox.cli.main import app
from typer.testing import CliRunner


def test_usage_json_preserves_api_response_and_encodes_dates(monkeypatch):
    monkeypatch.setattr(usage, "remote_url", lambda: "https://api.example")
    payload = {"measurement": "observed_state_estimate", "creations": 2, "vms": []}

    def request(method, path):
        assert method == "GET"
        parsed = urlsplit(path)
        assert parsed.path == "/v1/usage"
        assert parse_qs(parsed.query) == {
            "from": ["2026-11-01T00:00:00+03:00"],
            "to": ["2026-11-02T00:00:00Z"],
        }
        return payload

    monkeypatch.setattr(usage, "request", request)
    result = CliRunner().invoke(
        app,
        ["usage", "--json", "--from", "2026-11-01T00:00:00+03:00", "--to", "2026-11-02T00:00:00Z"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_usage_direct_mode_does_not_call_aws_or_service(monkeypatch):
    monkeypatch.setattr(usage, "remote_url", lambda: "")
    monkeypatch.setattr(usage, "request", lambda *a: (_ for _ in ()).throw(AssertionError()))
    result = CliRunner().invoke(app, ["usage"])
    assert result.exit_code == 1
    assert "requires hosted mode" in result.output


def test_usage_readable_output_shows_uncertainty(monkeypatch):
    monkeypatch.setattr(usage, "remote_url", lambda: "https://api.example")
    monkeypatch.setattr(
        usage,
        "request",
        lambda *a: {
            "period": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"},
            "tracking_started_at": "2026-11-01T00:00:00Z",
            "current": dict(running=1, suspended=0, unknown=1, other=0),
            "creations": 2,
            "vms": [],
            "totals": dict(running_seconds=10, suspended_seconds=0, unknown_seconds=80),
            "refresh_failed_vm_ids": ["vm-1"],
        },
    )
    result = CliRunner().invoke(app, ["usage"])
    assert result.exit_code == 0, result.output
    assert "not billing" in result.output
    assert "could not be refreshed" in result.output
