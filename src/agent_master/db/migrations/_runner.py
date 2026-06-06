"""Tiny hand-rolled migration runner.

Spec lifted verbatim from doc/06-architecture.md §Schema 迁移:
    - schema_version(version INT PRIMARY KEY)
    - for each NNN_*.sql in order, if NNN > current: executescript + bump

No alembic, no SQLAlchemy. The whole runner is ~30 lines on purpose.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent


def _current_version(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row is None:
        return 0
    val = row[0]
    return int(val) if val is not None else 0


def migrate(db: sqlite3.Connection) -> list[int]:
    """Apply pending migrations. Returns the versions that were applied."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    current = _current_version(db)
    applied: list[int] = []

    for path in sorted(MIGRATIONS_DIR.glob("[0-9]*.sql")):
        version = int(path.stem.split("_", 1)[0])
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        db.executescript(sql)
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        applied.append(version)

    return applied
