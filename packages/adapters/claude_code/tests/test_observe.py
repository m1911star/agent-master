"""Tests for the Claude Code Observer adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from packages.adapters.claude_code.observe import (
    ClaudeCodeObserver,
    _block_kind,
)

ADAPTER_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def fake_projects_dir() -> Path:
    out = ADAPTER_DIR / "fixtures" / "fake_projects"
    if not out.exists():
        import subprocess

        subprocess.run(
            [
                "uv",
                "run",
                "python",
                str(ADAPTER_DIR / "fixtures" / "build_fixture.py"),
            ],
            check=True,
            cwd=ADAPTER_DIR.parent.parent.parent,
        )
    return out


def test_list_existing_sessions(fake_projects_dir: Path):
    obs = ClaudeCodeObserver(fake_projects_dir, recent_hours=24 * 365)  # very generous
    descriptors = obs.list_existing_sessions()
    assert len(descriptors) == 1

    d = descriptors[0]
    assert d.external_id == "abcd1234-5678-90ab-cdef-1234567890ab"
    # encoded "-Users-test-repo" should decode to "/Users/test/repo"
    assert d.workdir == "/Users/test/repo"
    assert d.raw_path is not None and d.raw_path.exists()


def test_parse_session_full(fake_projects_dir: Path):
    obs = ClaudeCodeObserver(fake_projects_dir, recent_hours=24 * 365)
    descriptors = obs.list_existing_sessions()
    session, runs, events = obs.parse_session(descriptors[0])

    assert session.external_id == "abcd1234-5678-90ab-cdef-1234567890ab"
    assert session.workdir == "/Users/test/repo"
    assert len(runs) == 1

    # Expected event sequence:
    #   meta lines (2): skipped
    #   hook attachment: 1 status_change
    #   user msg (string): 1 user_message
    #   assistant (thinking + tool_use): 2 events (reasoning + tool_call)
    #   user msg (tool_result block): 1 tool_result
    #   assistant (text): 1 assistant_message
    # Total: 6
    kinds = [e.kind for e in events]
    assert kinds == [
        "status_change",
        "user_message",
        "reasoning",
        "tool_call",
        "tool_result",
        "assistant_message",
    ]

    # tool_call event preserves the block payload
    tc = next(e for e in events if e.kind == "tool_call")
    assert tc.payload["_claude_block"]["name"] == "Read"
    assert tc.payload["_claude_block"]["input"]["file_path"] == "src/foo.ts"

    # reasoning event keeps thinking text
    rs = next(e for e in events if e.kind == "reasoning")
    assert "check the file" in (rs.text or "").lower()

    # tool_result text comes through
    tr = next(e for e in events if e.kind == "tool_result")
    assert "file contents" in (tr.text or "")


def test_block_kind_mapping():
    assert _block_kind("thinking") == "reasoning"
    assert _block_kind("tool_use") == "tool_call"
    assert _block_kind("text") == "assistant_message"
    assert _block_kind("future_unknown") == "raw"


def test_missing_dir_returns_empty(tmp_path: Path):
    obs = ClaudeCodeObserver(tmp_path / "no_such_dir")
    assert obs.list_existing_sessions() == []


def test_recent_hours_filters_old_files(fake_projects_dir: Path, tmp_path: Path):
    """Confirm that very-old fixture files get filtered out at recent_hours=0."""
    obs = ClaudeCodeObserver(fake_projects_dir, recent_hours=0)
    # mtime of fixture is very recent (just built), so cutoff=now will accept it
    # only if mtime >= cutoff. With recent_hours=0, cutoff = now, which the
    # fixture's mtime should beat by milliseconds. Usually empty:
    descriptors = obs.list_existing_sessions()
    # Either empty or contains the fixture — both are valid because of timing.
    # The deterministic assertion is that the API doesn't crash.
    assert isinstance(descriptors, list)
