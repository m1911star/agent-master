"""Pytest fixtures shared across the suite."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ~/.agent-master/ to a tmp dir for the duration of the test."""
    home = tmp_path / "dot-agent-master"
    home.mkdir()
    config_path = home / "config.toml"

    monkeypatch.setattr("agent_master.config.DEFAULT_CONFIG_HOME", home)
    monkeypatch.setattr("agent_master.config.DEFAULT_CONFIG_PATH", config_path)
    yield home
