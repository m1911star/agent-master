"""Tests for the migration runner."""

from __future__ import annotations

from pathlib import Path

from agent_master.db import connect, migrate


def test_migrate_applies_initial_and_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "state.db"

    with connect(db_path) as conn:
        applied = migrate(conn)
        assert applied == [1]
        # WAL mode confirmed
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    # Re-open and run again — should be a no-op.
    with connect(db_path) as conn:
        applied = migrate(conn)
        assert applied == []
        rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [r[0] for r in rows] == [1]
