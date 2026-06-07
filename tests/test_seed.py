"""Tests for mock data seeding."""

from __future__ import annotations

from pathlib import Path

from agent_master.db import connect, migrate
from agent_master.seed import seed_mock_data, wipe_mock_data


def test_seed_populates_all_tables(tmp_path: Path):
    db_path = tmp_path / "state.db"
    summary = seed_mock_data(db_path)

    assert summary["agents"] == 3
    assert summary["sessions"] == 6
    assert summary["runs"] >= 6  # at least 1 per session, often more
    assert summary["events"] >= 50  # generous floor for variety
    assert summary["artifacts"] == 4
    assert summary["approvals_pending"] == 2
    assert summary["budgets"] == 1
    assert summary["rules"] == 1


def test_seed_covers_all_event_kinds(tmp_path: Path):
    """For UI development we need at least one event of each standard kind."""
    db_path = tmp_path / "state.db"
    seed_mock_data(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT kind FROM events"
        ).fetchall()
    kinds = {r[0] for r in rows}

    # All the kinds the UI needs to style differently
    required = {
        "session_start", "run_start", "user_message", "assistant_message",
        "reasoning", "tool_call", "tool_result", "artifact_created",
        "run_end",
    }
    missing = required - kinds
    assert not missing, f"missing event kinds: {missing}"


def test_seed_creates_topology(tmp_path: Path):
    """Parent/child session relationship must exist for the topology view."""
    db_path = tmp_path / "state.db"
    seed_mock_data(db_path)

    with connect(db_path) as conn:
        # At least one parent + 2 children pointing at it
        rows = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE parent_session_id IS NOT NULL"
        ).fetchone()
    assert rows[0] >= 2


def test_seed_reset_replaces_existing(tmp_path: Path):
    db_path = tmp_path / "state.db"
    seed_mock_data(db_path)
    seed_mock_data(db_path, reset=True)
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM agents").fetchone()
    assert row[0] == 3  # not 6


def test_wipe_clears_everything(tmp_path: Path):
    db_path = tmp_path / "state.db"
    seed_mock_data(db_path)
    wipe_mock_data(db_path)
    with connect(db_path) as conn:
        for table in ("agents", "sessions", "runs", "events", "artifacts"):
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert row[0] == 0, f"{table} not empty after wipe"
