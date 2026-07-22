"""Tests for `asb infra output` labeling and `--all` (no AWS needed)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_sandbox.cli.main import app
from agent_sandbox.infra.state import InfraStateStore

runner = CliRunner()


def _seed_state(tmp_path, monkeypatch):
    path = tmp_path / "infra-state.json"
    monkeypatch.setenv("AGENT_SANDBOX_INFRA_STATE", str(path))
    store = InfraStateStore(path=path)
    # The default sandbox.yaml in the repo is project agent-sandbox-os / stack dev.
    a = store.load("agent-sandbox-os", "dev")
    a.outputs = {"image_arn": "arn:img:a", "execution_role_arn": "arn:role:a"}
    store.save(a)
    b = store.load("proj-b", "prod")
    b.outputs = {"image_arn": "arn:img:b"}
    store.save(b)
    return path


def test_output_full_dump_is_labeled(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch)
    result = runner.invoke(app, ["infra", "output"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["project"] == "agent-sandbox-os"
    assert data["stack"] == "dev"
    assert data["image_arn"] == "arn:img:a"


def test_output_project_by_name(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch)
    result = runner.invoke(app, ["infra", "output", "project"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "agent-sandbox-os"


def test_output_single_value_unchanged(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch)
    result = runner.invoke(app, ["infra", "output", "image_arn"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "arn:img:a"


def test_output_empty_state_is_bare(tmp_path, monkeypatch):
    # No state seeded (never provisioned, or destroyed): no fabricated label.
    path = tmp_path / "infra-state.json"
    monkeypatch.setenv("AGENT_SANDBOX_INFRA_STATE", str(path))
    result = runner.invoke(app, ["infra", "output"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {}


def test_output_name_on_empty_state_fails(tmp_path, monkeypatch):
    path = tmp_path / "infra-state.json"
    monkeypatch.setenv("AGENT_SANDBOX_INFRA_STATE", str(path))
    result = runner.invoke(app, ["infra", "output", "project"])
    assert result.exit_code != 0


def test_output_all_lists_every_stack(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch)
    result = runner.invoke(app, ["infra", "output", "--all"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] == 2
    labels = {(s["project"], s["stack"]) for s in data["stacks"]}
    assert labels == {("agent-sandbox-os", "dev"), ("proj-b", "prod")}
