"""Keep CLI tests independent of the developer's installed hosted configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolated_client_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VMNEST_CONFIG", str(tmp_path / "vmnest-config.json"))
