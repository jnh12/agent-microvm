"""Tests for the infra JSON state store."""

from __future__ import annotations

from agent_sandbox.infra.state import InfraStateStore


def test_state_roundtrip_and_managed_flags(tmp_path):
    path = tmp_path / "infra-state.json"
    store = InfraStateStore(path=path)

    state = store.load("proj", "dev")
    state.set_resource("role", "iam-role", "agent-sandbox-exec", managed=True)
    state.set_resource("bucket", "s3-bucket", "my-bucket", managed=False)
    state.outputs = {"image_arn": "arn:img", "execution_role_arn": "arn:role"}
    store.save(state)

    # Fresh store instance reads from disk.
    reloaded = InfraStateStore(path=path).load("proj", "dev")
    assert reloaded.get_resource("role").managed is True
    assert reloaded.get_resource("bucket").managed is False
    assert reloaded.outputs["image_arn"] == "arn:img"


def test_list_stacks(tmp_path):
    path = tmp_path / "infra-state.json"
    store = InfraStateStore(path=path)
    assert store.list_stacks() == []
    for project, stack in [("proj-a", "dev"), ("proj-b", "prod")]:
        state = store.load(project, stack)
        state.outputs = {"image_arn": f"arn:{project}"}
        store.save(state)

    pairs = set(InfraStateStore(path=path).list_stacks())
    assert pairs == {("proj-a", "dev"), ("proj-b", "prod")}


def test_state_clear(tmp_path):
    path = tmp_path / "infra-state.json"
    store = InfraStateStore(path=path)
    state = store.load("proj", "dev")
    state.set_resource("role", "iam-role", "r", managed=True)
    store.save(state)

    store.clear("proj", "dev")
    assert InfraStateStore(path=path).load("proj", "dev").resources == {}


def test_state_stacks_are_isolated(tmp_path):
    path = tmp_path / "infra-state.json"
    store = InfraStateStore(path=path)
    dev = store.load("proj", "dev")
    dev.set_resource("role", "iam-role", "dev-role", managed=True)
    store.save(dev)
    prod = store.load("proj", "prod")
    prod.set_resource("role", "iam-role", "prod-role", managed=True)
    store.save(prod)

    fresh = InfraStateStore(path=path)
    assert fresh.load("proj", "dev").get_resource("role").id == "dev-role"
    assert fresh.load("proj", "prod").get_resource("role").id == "prod-role"
