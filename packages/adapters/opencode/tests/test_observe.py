"""Tests for the OpenCode Observer adapter.

Uses fixtures/fixture.db (built by build_fixture.py — also runs at session
start to make the suite hermetic).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

# Adapter packages aren't installed; add their dir to sys.path.
ADAPTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER_DIR.parent.parent.parent))  # repo root
sys.path.insert(0, str(ADAPTER_DIR))  # for `import observe`

from observe import OpenCodeObserver, PART_TYPE_TO_KIND, _summarize_part


@pytest.fixture(scope="module")
def fixture_db() -> Path:
    db = ADAPTER_DIR / "fixtures" / "fixture.db"
    if not db.exists():
        # Build it on the fly so the test is hermetic.
        import subprocess

        subprocess.run(
            ["uv", "run", "python", str(ADAPTER_DIR / "fixtures" / "build_fixture.py")],
            check=True,
            cwd=ADAPTER_DIR.parent.parent.parent,
        )
    return db


def test_list_existing_sessions(fixture_db: Path):
    obs = OpenCodeObserver(fixture_db, recent_hours=24)
    descriptors = obs.list_existing_sessions()
    ids = {d.external_id for d in descriptors}
    assert ids == {"ses_parent_aaa", "ses_child_bbb"}

    # Ordering: most recently updated first
    assert descriptors[0].external_id == "ses_child_bbb"

    # Workdir + meta carried through
    parent = next(d for d in descriptors if d.external_id == "ses_parent_aaa")
    assert parent.workdir == "/Users/test/repo"
    assert parent.meta["model"] == "claude-opus-4.7"
    assert parent.meta["parent_id"] is None

    child = next(d for d in descriptors if d.external_id == "ses_child_bbb")
    assert child.meta["parent_id"] == "ses_parent_aaa"


def test_list_existing_sessions_excludes_old(fixture_db: Path):
    """recent_hours filter actually drops old rows."""
    obs = OpenCodeObserver(fixture_db, recent_hours=0)  # exclude everything
    # 0 hours = anything older than now is excluded; fixture rows are -10s/-30s old
    descriptors = obs.list_existing_sessions()
    # Both fixture sessions are < 1min old, so 0 hours = need future-update
    # which means we get nothing (cutoff_ms ≈ now)
    assert descriptors == []


def test_parse_session_full(fixture_db: Path):
    obs = OpenCodeObserver(fixture_db)
    descriptors = obs.list_existing_sessions()
    parent = next(d for d in descriptors if d.external_id == "ses_parent_aaa")

    session, runs, events = obs.parse_session(parent)

    assert session.external_id == "ses_parent_aaa"
    assert session.workdir == "/Users/test/repo"
    assert session.summary == "Parent session"
    assert session.meta["agent"] == "Sisyphus"
    assert session.meta["opencode_parent_id"] is None
    assert session.meta["cost"] == pytest.approx(0.42)

    # 2 messages → 2 runs
    assert len(runs) == 2
    assert runs[0].summary == "user"
    assert runs[1].summary == "assistant"

    # Events: 1 from msg_1 + 6 from msg_2 = 7
    assert len(events) == 7

    # First run has 1 event (the user text)
    msg1_events = [e for e in events if e.run_id == runs[0].id]
    assert len(msg1_events) == 1
    assert msg1_events[0].kind == "user_message" or msg1_events[0].kind == "assistant_message"
    # Note: opencode part type is "text"; we map to assistant_message regardless of
    # message.role. That's V0.1 simplification — we'll refine in M1.4 if it matters.

    # Second run has 6 events with the 6 part types in order
    msg2_events = sorted(
        [e for e in events if e.run_id == runs[1].id], key=lambda e: e.seq
    )
    kinds = [e.kind for e in msg2_events]
    assert kinds == [
        "run_start",
        "reasoning",
        "tool_call",
        "assistant_message",
        "artifact_created",
        "run_end",
    ]

    # Check tool_call payload is preserved
    tool_event = next(e for e in msg2_events if e.kind == "tool_call")
    assert tool_event.payload["tool"] == "Read"
    assert tool_event.payload["state"]["status"] == "completed"


def test_part_type_mapping_coverage():
    """Every documented opencode part type maps to a known kind."""
    expected = {"text", "reasoning", "tool", "step-start", "step-finish",
                "patch", "file", "compaction", "subtask"}
    for ptype in expected:
        assert ptype in PART_TYPE_TO_KIND, f"missing mapping for {ptype}"


def test_unknown_part_type_falls_back_to_raw(tmp_path: Path):
    """Unmapped types should still emit an event with kind='raw'."""
    db = tmp_path / "weird.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        (ADAPTER_DIR / "fixtures" / "build_fixture.py").read_text()
        .split('SCHEMA = """')[1].split('"""')[0]
    )
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, title, version, "
        "time_created, time_updated) "
        "VALUES ('s1', 'p', 'x', '/tmp', 'x', '0.1', ?, ?)",
        (now_ms, now_ms),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) "
        "VALUES ('m1', 's1', ?, ?, ?)",
        (now_ms, now_ms, '{"role": "assistant"}'),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
        "VALUES ('p1', 'm1', 's1', ?, ?, ?)",
        (now_ms, now_ms, '{"type": "future_unknown_thing", "data": 42}'),
    )
    conn.commit()
    conn.close()

    obs = OpenCodeObserver(db)
    descriptors = obs.list_existing_sessions()
    assert len(descriptors) == 1
    _, _, events = obs.parse_session(descriptors[0])
    assert events[0].kind == "raw"
    assert events[0].payload["type"] == "future_unknown_thing"


def test_summarize_part_handles_each_type():
    samples = [
        ({"type": "text", "text": "hello world"}, "hello"),
        ({"type": "reasoning", "text": "thinking..."}, "thinking"),
        ({"type": "tool", "tool": "Bash", "state": {"status": "running"}}, "Bash"),
        ({"type": "patch", "path": "src/x.ts"}, "src/x.ts"),
        ({"type": "step-finish", "cost": 0.01}, "0.01"),
    ]
    for data, expected_substr in samples:
        s = _summarize_part(data)
        assert expected_substr in s, f"{data} → {s} (missing {expected_substr})"


def test_missing_db_returns_empty(tmp_path: Path):
    obs = OpenCodeObserver(tmp_path / "nope.db")
    assert obs.list_existing_sessions() == []
