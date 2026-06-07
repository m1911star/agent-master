"""Tests for the migration runner and 002_core_schema."""

from __future__ import annotations

from pathlib import Path

from agent_master.db import connect, migrate


def test_migrate_applies_all_and_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "state.db"

    with connect(db_path) as conn:
        applied = migrate(conn)
        # 001 placeholder + 002 core schema + 003 budget timestamps
        assert applied == [1, 2, 3]
        # WAL mode confirmed
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        # Foreign keys enforced
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_on == 1

    # Re-open and run again — should be a no-op.
    with connect(db_path) as conn:
        applied = migrate(conn)
        assert applied == []
        rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]


def test_002_creates_all_11_tables(tmp_path: Path):
    db_path = tmp_path / "state.db"
    expected_tables = {
        "agents",
        "sessions",
        "runs",
        "events",
        "tasks",
        "approvals",
        "artifacts",
        "budgets",
        "rules",
        "workflows",
        "audit_log",
    }
    with connect(db_path) as conn:
        migrate(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_version'"
        ).fetchall()
        actual = {r[0] for r in rows}
        assert expected_tables <= actual, f"missing: {expected_tables - actual}"


def test_002_foreign_keys_cascade(tmp_path: Path):
    """Deleting an agent should cascade to its sessions per doc/01 §关系约束."""
    db_path = tmp_path / "state.db"
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute(
            "INSERT INTO agents (id, name, adapter_type, status, created_at, updated_at) "
            "VALUES ('a1', 'test', 'claude_code', 'idle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, last_active_at, status) "
            "VALUES ('s1', 'a1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'active')"
        )
        # Delete agent -> session goes too
        conn.execute("DELETE FROM agents WHERE id = 'a1'")
        rows = conn.execute("SELECT * FROM sessions WHERE id = 's1'").fetchall()
        assert rows == []


def test_002_foreign_keys_set_null(tmp_path: Path):
    """Deleting a parent session should null parent_session_id, not cascade."""
    db_path = tmp_path / "state.db"
    with connect(db_path) as conn:
        migrate(conn)
        conn.execute(
            "INSERT INTO agents (id, name, adapter_type, status, created_at, updated_at) "
            "VALUES ('a1', 'test', 'claude_code', 'idle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, last_active_at, status) "
            "VALUES ('s_parent', 'a1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'active')"
        )
        conn.execute(
            "INSERT INTO sessions (id, agent_id, parent_session_id, started_at, last_active_at, status) "
            "VALUES ('s_child', 'a1', 's_parent', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'active')"
        )
        conn.execute("DELETE FROM sessions WHERE id = 's_parent'")
        row = conn.execute(
            "SELECT parent_session_id FROM sessions WHERE id = 's_child'"
        ).fetchone()
        assert row[0] is None


def test_002_event_autoincrement(tmp_path: Path):
    """Events table uses AUTOINCREMENT id (high-frequency table)."""
    db_path = tmp_path / "state.db"
    with connect(db_path) as conn:
        migrate(conn)
        # Set up agent + session + run
        conn.executescript(
            """
            INSERT INTO agents (id, name, adapter_type, status, created_at, updated_at)
              VALUES ('a1', 't', 'claude_code', 'idle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO sessions (id, agent_id, started_at, last_active_at, status)
              VALUES ('s1', 'a1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'active');
            INSERT INTO runs (id, session_id, trigger, started_at, status)
              VALUES ('r1', 's1', 'manual', '2026-01-01T00:00:00Z', 'running');
            """
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO events (run_id, seq, ts, created_at, kind) "
                "VALUES ('r1', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'user_message')",
                (i,),
            )
        ids = [r[0] for r in conn.execute("SELECT id FROM events ORDER BY id").fetchall()]
        assert ids == [1, 2, 3]
