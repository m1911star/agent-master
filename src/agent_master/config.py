"""Configuration loader for agent-master.

Reads ~/.agent-master/config.toml (creating a default on first run).
Uses stdlib tomllib for reads, tomli-w for the initial write.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

DEFAULT_CONFIG_HOME = Path("~/.agent-master").expanduser()
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_HOME / "config.toml"


DEFAULT_CONFIG: dict[str, Any] = {
    "daemon": {
        "host": "127.0.0.1",
        "port": 8765,
        "log_level": "info",
    },
    "storage": {
        "db_path": "~/.agent-master/state.db",
        "backup_retention_days": 7,
    },
    "ui": {
        "auto_open_browser": True,
    },
    "adapters": {
        "enabled": ["opencode", "claude_code", "hermes"],
        "opencode": {
            "db_path": "~/.local/share/opencode/opencode.db",
            "poll_ms": 200,
            "recent_hours": 24,
        },
        "claude_code": {
            "projects_dir": "~/.claude/projects",
            "poll_fallback_ms": 1000,
        },
        "hermes": {
            "db_path": "~/.hermes/state.db",
            "poll_ms": 200,
        },
    },
}


@dataclass
class DaemonConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "info"


@dataclass
class StorageConfig:
    db_path: Path = field(default_factory=lambda: Path("~/.agent-master/state.db").expanduser())
    backup_retention_days: int = 7


@dataclass
class Config:
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path = field(default_factory=lambda: DEFAULT_CONFIG_PATH)


def ensure_config_dir(home: Path = DEFAULT_CONFIG_HOME) -> Path:
    """Create ~/.agent-master/ (mode 0700-ish — best effort cross-platform)."""
    home.mkdir(parents=True, exist_ok=True)
    # POSIX systems: tighten perms. Windows just ignores.
    with contextlib.suppress(OSError):
        os.chmod(home, 0o755)
    return home


def write_default_config(path: Path | None = None) -> None:
    """Materialize DEFAULT_CONFIG to disk in TOML form."""
    target = path if path is not None else DEFAULT_CONFIG_PATH
    ensure_config_dir(target.parent)
    target.write_bytes(tomli_w.dumps(DEFAULT_CONFIG).encode("utf-8"))


def load_config(path: Path | None = None, *, create_if_missing: bool = True) -> Config:
    """Load (and on first run, create) the agent-master config file.

    Path resolution is late-bound (resolved at call time, not import time) so
    tests can monkeypatch DEFAULT_CONFIG_PATH and still get redirected.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    if not path.exists():
        if not create_if_missing:
            raise FileNotFoundError(path)
        write_default_config(path)

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    daemon_section = raw.get("daemon", {})
    daemon = DaemonConfig(
        host=daemon_section.get("host", "127.0.0.1"),
        port=int(daemon_section.get("port", 8765)),
        log_level=daemon_section.get("log_level", "info"),
    )

    storage_section = raw.get("storage", {})
    db_path = Path(
        storage_section.get("db_path", "~/.agent-master/state.db")
    ).expanduser()
    storage = StorageConfig(
        db_path=db_path,
        backup_retention_days=int(storage_section.get("backup_retention_days", 7)),
    )

    return Config(daemon=daemon, storage=storage, raw=raw, path=path)
