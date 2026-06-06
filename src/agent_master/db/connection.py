"""SQLite connection helper — WAL + sane pragmas."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite DB at db_path with WAL + foreign keys on."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    # WAL is the headline requirement; the rest are sane defaults for a local DB.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn
