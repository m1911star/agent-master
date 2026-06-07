"""Tests for the Hermes Observer adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from packages.adapters.hermes.observe import HermesObserver, ROLE_TO_KIND

ADAPTER_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def fixture_db() -> Path:
    db = ADAPTER_DIR / "fixtures" / "fixture.db"
    if not db.exists():
        import subprocess

        subprocess.run(
            ["uv", "run", "python", str(ADAPTER_DIR / "fixtures" / "build_fixture.py")],
            check=True,
            cwd=ADAPTER_DIR.parent.parent.parent,
        )
    return db


def test_list_existing_sessions(fixture_db: Path):
    obs = HermesObserver(fixture_db, recent_hours=24)
    descriptors = obs.list_existing_sessions()
    ids = {d.external_id for d in descriptors}
    assert ids == {"hms_parent", "hms_child"}

    # parent_id propagated
    parent = next(d for d in descriptors if d.external_id == "hms_parent")
    child = next(d for d in descriptors if d.external_id == "hms_child")
    assert parent.meta["parent_id"] is None
    assert child.meta["parent_id"] == "hms_parent"
    assert parent.meta["model"] == "claude-opus-4.7"


def test_parse_session_full(fixture_db: Path):
    obs = HermesObserver(fixture_db)
    descriptors = obs.list_existing_sessions()
    parent_desc = next(d for d in descriptors if d.external_id == "hms_parent")
    session, runs, events = obs.parse_session(parent_desc)

    assert session.external_id == "hms_parent"
    assert session.meta["hermes_parent_session_id"] is None
    assert session.meta["model"] == "claude-opus-4.7"
    assert session.status == "closed"
    assert session.ended_at is not None

    # V0.1: one Run per session
    assert len(runs) == 1
    run = runs[0]
    assert run.tokens_in == 5000
    assert run.tokens_out == 1500

    # 4 messages → events expanded:
    #   msg 1 (user)            → 1 event (user_message)
    #   msg 2 (assistant)       → 3 events (reasoning, assistant_message, tool_call)
    #   msg 3 (tool)            → 1 event (tool_result, with tool_name=Read)
    #   msg 4 (assistant)       → 1 event (assistant_message)
    # Total: 6 events
    assert len(events) == 6

    kinds = [e.kind for e in events]
    assert kinds == [
        "user_message",
        "reasoning",
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
    ]

    # tool_call payload preserves the call structure
    tc = next(e for e in events if e.kind == "tool_call")
    assert tc.payload["tool_call"]["function"]["name"] == "Read"

    # tool_result has tool_name in payload
    tr = next(e for e in events if e.kind == "tool_result")
    assert tr.payload["tool_name"] == "Read"

    # Sequence numbers are monotonically increasing
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert seqs[0] == 0
    assert len(set(seqs)) == len(seqs)  # unique


def test_role_kind_map_covers_known_roles():
    assert ROLE_TO_KIND["user"] == "user_message"
    assert ROLE_TO_KIND["assistant"] == "assistant_message"
    assert ROLE_TO_KIND["tool"] == "tool_result"
    assert ROLE_TO_KIND["system"] == "status_change"


def test_missing_db_returns_empty(tmp_path: Path):
    obs = HermesObserver(tmp_path / "nope.db")
    assert obs.list_existing_sessions() == []


def test_recent_hours_excludes_old(fixture_db: Path):
    obs = HermesObserver(fixture_db, recent_hours=0)
    # cutoff = now; all fixture sessions are 30-60s old, so excluded
    assert obs.list_existing_sessions() == []
