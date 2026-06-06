"""Tests for the config loader."""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_master.config import DEFAULT_CONFIG, load_config


def test_first_run_creates_default_config(tmp_config_home: Path):
    config_path = tmp_config_home / "config.toml"
    assert not config_path.exists()

    cfg = load_config()

    assert config_path.exists()
    assert cfg.daemon.host == "127.0.0.1"
    assert cfg.daemon.port == 8765
    assert cfg.daemon.log_level == "info"
    # storage.db_path gets expanduser'd, but starts from the default template.
    assert str(cfg.storage.db_path).endswith("state.db")

    on_disk = tomllib.loads(config_path.read_text())
    assert on_disk["daemon"]["port"] == DEFAULT_CONFIG["daemon"]["port"]
    assert on_disk["adapters"]["enabled"] == DEFAULT_CONFIG["adapters"]["enabled"]


def test_second_run_does_not_clobber_existing(tmp_config_home: Path):
    config_path = tmp_config_home / "config.toml"
    config_path.write_text(
        '[daemon]\nhost = "0.0.0.0"\nport = 9000\nlog_level = "debug"\n'
        '\n[storage]\ndb_path = "/tmp/somewhere.db"\nbackup_retention_days = 3\n'
    )

    cfg = load_config()

    assert cfg.daemon.host == "0.0.0.0"
    assert cfg.daemon.port == 9000
    assert cfg.daemon.log_level == "debug"
    assert str(cfg.storage.db_path) == "/tmp/somewhere.db"
    assert cfg.storage.backup_retention_days == 3
