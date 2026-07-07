"""Unit tests for the envelope helpers and host-path policy (no AWS needed)."""

from __future__ import annotations

from dataclasses import dataclass

from agent_sandbox_mcp.config import POLICY_ALLOWLIST, POLICY_UNRESTRICTED, Config
from agent_sandbox_mcp.envelope import cap_text, err, exec_result, ok


def test_ok_and_err_shapes():
    assert ok({"x": 1}) == {"ok": True, "data": {"x": 1}}
    e = err("boom", code="Oops", detail=42)
    assert e["ok"] is False
    assert e["error"] == {"code": "Oops", "message": "boom", "detail": 42}


def test_cap_text_no_truncation():
    out = cap_text("hello", 1000)
    assert out == {"text": "hello", "truncated": False}


def test_cap_text_truncates_with_metadata():
    out = cap_text("abcdefghij", 4)
    assert out["truncated"] is True
    assert out["text"] == "abcd"
    assert out["total_bytes"] == 10
    assert out["returned_bytes"] == 4


@dataclass
class _FakeResult:
    exit_code: int
    stdout_text: str
    stderr_text: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def test_exec_result_payload():
    payload = exec_result(_FakeResult(0, "out", "err"), 1000)
    assert payload["exit_code"] == 0
    assert payload["success"] is True
    assert payload["stdout"]["text"] == "out"
    assert payload["stderr"]["text"] == "err"


def _config(policy: str, paths: list[str]) -> Config:
    return Config(
        region=None,
        workdir="/work",
        verify_tls=True,
        max_output_bytes=1024,
        default_timeout_ms=1000,
        host_path_policy=policy,
        host_paths=paths,
    )


def test_host_path_allowlist(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    cfg = _config(POLICY_ALLOWLIST, [str(root)])
    assert cfg.host_path_allowed(str(root / "file.txt")) is True
    assert cfg.host_path_allowed(str(tmp_path / "other.txt")) is False


def test_host_path_unrestricted(tmp_path):
    cfg = _config(POLICY_UNRESTRICTED, [str(tmp_path / "allowed")])
    assert cfg.host_path_allowed("/anywhere/at/all") is True
