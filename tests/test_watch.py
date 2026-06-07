"""Tests for the watch layer.

Real filesystem + real SQLite, no mocks. We're verifying actual platform
behavior — mocking would defeat the purpose.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from agent_master.watch import FSWatcher, JsonlTail, SqliteTailer


# ─── JSONL tail ────────────────────────────────────────────────────────────


def test_jsonl_tail_reads_new_lines(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text('{"a": 1}\n{"a": 2}\n')

    t = JsonlTail(f)
    rows = t.read_new()
    assert rows == [{"a": 1}, {"a": 2}]

    # Append more
    with f.open("a") as fp:
        fp.write('{"a": 3}\n')
    assert t.read_new() == [{"a": 3}]

    # Idempotent
    assert t.read_new() == []


def test_jsonl_tail_handles_partial_line(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text('{"a": 1}\n{"a": 2}\n{"par')  # partial line

    t = JsonlTail(f)
    rows = t.read_new()
    assert rows == [{"a": 1}, {"a": 2}]

    # Complete the partial line later
    with f.open("a") as fp:
        fp.write('tial": true}\n')
    assert t.read_new() == [{"partial": True}]


def test_jsonl_tail_handles_truncate(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text('{"a": 1}\n{"a": 2}\n')

    t = JsonlTail(f)
    t.read_new()

    # Truncate by overwriting smaller content
    f.write_text('{"new": 1}\n')

    # Should detect truncate and rewind
    assert t.read_new() == [{"new": 1}]


def test_jsonl_tail_skips_malformed_lines(tmp_path: Path):
    f = tmp_path / "log.jsonl"
    f.write_text('{"ok": 1}\n!@#$%^\n{"ok": 2}\n')

    t = JsonlTail(f)
    rows = t.read_new()
    assert rows == [{"ok": 1}, {"ok": 2}]


def test_jsonl_tail_missing_file_returns_empty(tmp_path: Path):
    t = JsonlTail(tmp_path / "nope.jsonl")
    assert t.read_new() == []


# ─── SQLite tailer ─────────────────────────────────────────────────────────


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT)")
    for i in range(3):
        conn.execute("INSERT INTO events (msg) VALUES (?)", (f"msg-{i}",))
    conn.commit()
    conn.close()
    return db_path


def test_sqlite_tailer_polls_existing_rows(populated_db: Path):
    t = SqliteTailer(populated_db, "events")
    rows = t.poll()
    assert [r["msg"] for r in rows] == ["msg-0", "msg-1", "msg-2"]
    assert t.last_seen == 3
    t.close()


def test_sqlite_tailer_seed_skips_history(populated_db: Path):
    """seed() sets cursor to MAX so subsequent polls return only new rows."""
    t = SqliteTailer(populated_db, "events")
    t.seed()
    assert t.last_seen == 3
    rows = t.poll()
    assert rows == []

    # Outside writer adds a new row
    conn = sqlite3.connect(populated_db)
    conn.execute("INSERT INTO events (msg) VALUES (?)", ("msg-3",))
    conn.commit()
    conn.close()

    rows = t.poll()
    assert [r["msg"] for r in rows] == ["msg-3"]
    t.close()


def test_sqlite_tailer_context_manager(populated_db: Path):
    with SqliteTailer(populated_db, "events") as t:
        assert len(t.poll()) == 3
    # No assertion on connection state — just shouldn't raise.


# ─── FS watcher ────────────────────────────────────────────────────────────


def test_fswatcher_fires_callback_on_create(tmp_path: Path):
    seen: list[Path] = []
    done = threading.Event()

    def callback(path: Path, change) -> None:
        seen.append(path)
        done.set()

    w = FSWatcher([tmp_path], callback, debounce_ms=50)
    w.start()
    try:
        # Tiny delay so watcher is settled
        time.sleep(0.2)
        (tmp_path / "new.txt").write_text("hi")
        # Wait up to 3s for the callback
        assert done.wait(timeout=3.0), "callback never fired"
        assert any("new.txt" in str(p) for p in seen)
    finally:
        w.stop()


def test_fswatcher_silent_on_missing_paths(tmp_path: Path):
    """Watching nonexistent paths shouldn't raise."""
    nonexistent = tmp_path / "no_such_dir"
    w = FSWatcher([nonexistent], lambda p, c: None)
    w.start()
    time.sleep(0.1)  # let thread initialize
    w.stop()
    # If we get here without exception, the test passes.
