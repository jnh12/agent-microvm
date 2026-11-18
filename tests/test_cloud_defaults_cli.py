import json

import pytest
from agent_sandbox.accounts import AccountError
from agent_sandbox.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "hosted")
    seen = []

    def request(method, path, body=None):
        seen.append((method, path, body))
        return {
            "revision": 7 if method == "GET" else 8,
            "variables": ["API_KEY"],
            "startup_script": "echo private-script",
        }

    monkeypatch.setattr("agent_sandbox.cli.cloud_defaults.api", request)
    return seen


def test_stdin_set_never_echoes_secret_and_patches_only_key(calls):
    result = runner.invoke(
        app, ["defaults", "set", "API_KEY", "--stdin", "--json"], input="private-secret\n"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"name": "API_KEY", "status": "saved", "revision": 8}
    assert "private" not in result.output
    assert calls == [
        ("GET", "/v1/cloud-defaults", None),
        (
            "PATCH",
            "/v1/cloud-defaults",
            {"revision": 7, "variables": {"API_KEY": "private-secret"}},
        ),
    ]


def test_value_file_preserves_whitespace(calls, tmp_path):
    path = tmp_path / "value.txt"
    path.write_bytes(b"secret\r\n")
    result = runner.invoke(app, ["defaults", "set", "API_KEY", "--file", str(path), "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["variables"]["API_KEY"] == "secret\r\n"
    assert "secret" not in result.output


def test_list_hides_script_and_values(calls):
    result = runner.invoke(app, ["defaults", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "variables": ["API_KEY"],
        "revision": 7,
        "startup_script_configured": True,
    }
    assert "private" not in result.output
    human = runner.invoke(app, ["defaults", "list"])
    assert human.exit_code == 0 and "API_KEY" in human.output and "configured" in human.output
    assert "private" not in human.output


def test_remove_only_changes_selected_variable(calls):
    result = runner.invoke(app, ["defaults", "remove", "API_KEY", "--json"])
    assert result.exit_code == 0
    assert calls[-1][2] == {"revision": 7, "variables": {"API_KEY": None}}


@pytest.mark.parametrize(
    "args,body",
    [
        (["script", "--file", "-"], {"startup_script": "echo ready\n"}),
        (["script", "--clear"], {"startup_script": ""}),
    ],
)
def test_script_changes_only_script(calls, args, body):
    result = runner.invoke(app, ["defaults", *args, "--json"], input="echo ready\n")
    assert result.exit_code == 0, result.output
    assert calls[-1][2] == {"revision": 7, **body}
    assert "private" not in result.output


def test_script_file_and_read(calls, tmp_path):
    path = tmp_path / "setup.sh"
    path.write_bytes(b"echo ready\r\n")
    result = runner.invoke(app, ["defaults", "script", "--file", str(path), "--json"])
    assert result.exit_code == 0
    assert calls[-1][2] == {"revision": 7, "startup_script": "echo ready\n"}
    result = runner.invoke(app, ["defaults", "script", "--json"])
    assert json.loads(result.output)["startup_script"] == "echo private-script"


@pytest.mark.parametrize(
    "args",
    [
        ["set", "API_KEY", "--json"],
        ["set", "BAD-NAME", "--stdin"],
        ["set", "API_KEY", "--stdin", "--file", "unused"],
        ["script", "--file", "unused", "--clear"],
        ["set", "API_KEY", "--file", "missing-file-for-test"],
    ],
)
def test_invalid_input_fails_before_network(calls, args):
    result = runner.invoke(app, ["defaults", *args], input="private-secret")
    assert result.exit_code != 0 and not calls
    assert "private-secret" not in result.output


def test_size_limit_before_network(calls):
    result = runner.invoke(
        app, ["defaults", "set", "API_KEY", "--stdin", "--json"], input="x" * 4097
    )
    assert result.exit_code == 1 and not calls
    assert json.loads(result.output)["error"]["code"] == "INVALID_REQUEST"


def test_conflict_is_not_retried(calls, monkeypatch):
    def request(method, path, body=None):
        calls.append(method)
        if method == "PATCH":
            raise AccountError("DEFAULTS_CHANGED", "Reload before retrying.")
        return {"revision": 7}

    monkeypatch.setattr("agent_sandbox.cli.cloud_defaults.api", request)
    result = runner.invoke(
        app, ["defaults", "set", "API_KEY", "--stdin", "--json"], input="private-secret"
    )
    assert result.exit_code == 1 and calls == ["GET", "PATCH"]
    assert json.loads(result.output)["error"]["code"] == "DEFAULTS_CHANGED"
    assert "private-secret" not in result.output


def test_hosted_only(calls, monkeypatch):
    monkeypatch.setenv("VMNEST_MODE", "self-hosted")
    result = runner.invoke(app, ["defaults", "list", "--json"])
    assert result.exit_code != 0 and not calls


def test_interactive_prompt_hides_value(calls, monkeypatch):
    monkeypatch.setattr("agent_sandbox.cli.cloud_defaults.interactive_terminal", lambda: True)

    def prompt(*args, **kwargs):
        assert kwargs["hide_input"] is True
        return "private-secret"

    monkeypatch.setattr("agent_sandbox.cli.cloud_defaults.typer.prompt", prompt)
    result = runner.invoke(app, ["defaults", "set", "API_KEY"])
    assert result.exit_code == 0, result.output
    assert "private-secret" not in result.output


@pytest.mark.parametrize(
    "value,expected",
    [
        ("private-secret\r", "private-secret\r"),
        ("private-secret\r\r\n", "private-secret\r"),
        ("private-secret\n\n", "private-secret\n"),
        ("private-secret\r\n", "private-secret"),
        ("private-secret\n", "private-secret"),
        ("private-secret", "private-secret"),
    ],
)
def test_stdin_removes_only_one_newline(calls, monkeypatch, value, expected):
    import io

    from agent_sandbox.cli import cloud_defaults

    # Use an exact text stream rather than CliRunner's universal-newline stdin.
    original_read_input = cloud_defaults.read_input

    def read_input(filename, limit):
        monkeypatch.setattr("sys.stdin", io.StringIO(value))
        return original_read_input(filename, limit)

    monkeypatch.setattr(cloud_defaults, "read_input", read_input)
    result = runner.invoke(app, ["defaults", "set", "API_KEY", "--stdin", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["variables"] == {"API_KEY": expected}
    assert "private-secret" not in result.output
