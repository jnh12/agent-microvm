"""Tests for multi-project config loading, default resolution, and state safety."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from agent_sandbox.infra.config import SetupError, load_setup, load_setups
from agent_sandbox.infra.state import InfraStateStore


def _make_guest(d: Path) -> None:
    guest = d / "guest"
    guest.mkdir(parents=True, exist_ok=True)
    (guest / "Dockerfile").write_text("FROM scratch\n")


def _doc(project: str, stack: str = "dev") -> str:
    return (
        f"project: {project}\n"
        f"stack: {stack}\n"
        "region: us-east-1\n"
        "image:\n"
        "  guest_dir: ./guest\n"
    )


# -- load_setups ------------------------------------------------------------


def test_load_setups_multiple_files(tmp_path):
    _make_guest(tmp_path)
    (tmp_path / "a.yaml").write_text(_doc("proj-a"))
    (tmp_path / "b.yaml").write_text(_doc("proj-b"))
    cfgs = load_setups([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert sorted(c.project for c in cfgs) == ["proj-a", "proj-b"]


def test_load_setups_directory_expansion(tmp_path):
    _make_guest(tmp_path)
    (tmp_path / "a.yaml").write_text(_doc("proj-a"))
    (tmp_path / "b.yml").write_text(_doc("proj-b"))
    (tmp_path / "notes.txt").write_text("ignored")
    cfgs = load_setups([str(tmp_path)])
    assert sorted(c.project for c in cfgs) == ["proj-a", "proj-b"]


def test_load_setups_multi_document_file(tmp_path):
    _make_guest(tmp_path)
    (tmp_path / "multi.yaml").write_text(_doc("p1") + "---\n" + _doc("p2"))
    cfgs = load_setups([str(tmp_path / "multi.yaml")])
    assert sorted(c.project for c in cfgs) == ["p1", "p2"]


def test_load_setups_stack_override(tmp_path):
    _make_guest(tmp_path)
    (tmp_path / "a.yaml").write_text(_doc("proj-a", stack="dev"))
    cfgs = load_setups([str(tmp_path / "a.yaml")], stack="prod")
    assert cfgs[0].stack == "prod"


def test_load_setups_rejects_non_mapping_document(tmp_path):
    _make_guest(tmp_path)
    (tmp_path / "bad.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(SetupError):
        load_setups([str(tmp_path / "bad.yaml")])


def test_load_setups_missing_path(tmp_path):
    with pytest.raises(SetupError):
        load_setups([str(tmp_path / "nope.yaml")])


# -- default filename resolution -------------------------------------------


def test_default_resolution_prefers_sandbox_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_SANDBOX_SETUP", raising=False)
    _make_guest(tmp_path)
    (tmp_path / "sandbox.yaml").write_text(_doc("from-sandbox"))
    assert load_setup().project == "from-sandbox"


def test_default_resolution_falls_back_to_legacy_setup_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_SANDBOX_SETUP", raising=False)
    _make_guest(tmp_path)
    (tmp_path / "setup.yaml").write_text(_doc("from-legacy"))
    assert load_setup().project == "from-legacy"


# -- concurrent state store -------------------------------------------------


def test_state_store_concurrent_saves_do_not_clobber(tmp_path):
    store = InfraStateStore(path=tmp_path / "s.json")

    def worker(i: int) -> None:
        state = store.load(f"proj{i}", "dev")
        state.set_resource("role", "iam-role", f"r{i}", managed=True)
        state.outputs = {"image_arn": f"arn{i}"}
        store.save(state)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reloaded = InfraStateStore(path=tmp_path / "s.json")
    for i in range(20):
        state = reloaded.load(f"proj{i}", "dev")
        assert state.get_resource("role").id == f"r{i}"
        assert state.outputs["image_arn"] == f"arn{i}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
