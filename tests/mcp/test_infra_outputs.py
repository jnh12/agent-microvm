"""Unit tests for the `infra_outputs` tool's resolution (no AWS needed)."""

from __future__ import annotations

import json

import pytest

from agent_sandbox_mcp.tools.infra import build_outputs


class _Cfg:
    region = "us-east-1"


@pytest.fixture
def infra_state(tmp_path, monkeypatch):
    """Point the infra state store at a temp file with two projects."""
    path = tmp_path / "infra-state.json"
    path.write_text(
        json.dumps(
            {
                "stacks": {
                    "proj-a/dev": {
                        "resources": {},
                        "outputs": {
                            "image_arn": "arn:img:a",
                            "execution_role_arn": "arn:role:a",
                            "build_bucket": "bkt-a",
                        },
                    },
                    "proj-b/prod": {
                        "resources": {},
                        "outputs": {
                            "image_arn": "arn:img:b",
                            "execution_role_arn": "arn:role:b",
                        },
                    },
                }
            }
        )
    )
    monkeypatch.setenv("AGENT_SANDBOX_INFRA_STATE", str(path))
    # These would otherwise override infra values via the env source.
    monkeypatch.delenv("AGENT_SANDBOX_IMAGE_ARN", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_EXECUTION_ROLE_ARN", raising=False)
    return path


def test_build_outputs_targets_project(infra_state):
    out = build_outputs(_Cfg(), project="proj-a")
    assert out["project"] == "proj-a"
    assert out["stack"] == "dev"
    assert out["ready"] is True
    assert out["image_arn"] == {"value": "arn:img:a", "source": "infra"}
    assert out["role_arn"] == {"value": "arn:role:a", "source": "infra"}
    assert out["build_bucket"] == "bkt-a"


def test_build_outputs_stack_override(infra_state):
    out = build_outputs(_Cfg(), project="proj-b", stack="prod")
    assert (out["project"], out["stack"]) == ("proj-b", "prod")
    assert out["image_arn"]["value"] == "arn:img:b"


def test_build_outputs_missing_project_is_not_ready(infra_state):
    out = build_outputs(_Cfg(), project="nope")
    assert out["project"] == "nope"
    assert out["ready"] is False
    assert out["image_arn"] == {"value": None, "source": "unset"}


def test_env_var_overrides_infra_source(infra_state, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_IMAGE_ARN", "arn:img:env")
    out = build_outputs(_Cfg(), project="proj-a")
    assert out["image_arn"] == {"value": "arn:img:env", "source": "env"}
    # Role still comes from infra state.
    assert out["role_arn"]["source"] == "infra"
