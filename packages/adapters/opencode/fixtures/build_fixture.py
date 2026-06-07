"""Build a minimal opencode-shape SQLite fixture for adapter tests.

Run: uv run python packages/adapters/opencode/fixtures/build_fixture.py

This produces fixture.db in the same directory with a 2-session, 4-message,
8-part dataset that mirrors the real schema closely enough for parsing tests.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "fixture.db"


SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,
    slug TEXT NOT NULL,
    directory TEXT NOT NULL,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    share_url TEXT,
    summary_additions INTEGER,
    summary_deletions INTEGER,
    summary_files INTEGER,
    summary_diffs TEXT,
    revert TEXT,
    permission TEXT,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    time_compacting INTEGER,
    time_archived INTEGER,
    workspace_id TEXT,
    path TEXT,
    agent TEXT,
    model TEXT,
    cost REAL DEFAULT 0 NOT NULL,
    tokens_input INTEGER DEFAULT 0 NOT NULL,
    tokens_output INTEGER DEFAULT 0 NOT NULL,
    tokens_reasoning INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_read INTEGER DEFAULT 0 NOT NULL,
    tokens_cache_write INTEGER DEFAULT 0 NOT NULL,
    metadata TEXT
);

CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.executescript(SCHEMA)

    now_ms = int(time.time() * 1000)

    # Two sessions: parent + child (sidechain-like)
    sessions = [
        (
            "ses_parent_aaa",
            None,
            "/Users/test/repo",
            "Parent session",
            now_ms - 60_000,
            now_ms - 30_000,
            "Sisyphus",
            "claude-opus-4.7",
            0.42,
            5000,
            1200,
        ),
        (
            "ses_child_bbb",
            "ses_parent_aaa",
            "/Users/test/repo",
            "Spawned subtask",
            now_ms - 50_000,
            now_ms - 10_000,
            "Sisyphus",
            "claude-haiku-4.5",
            0.05,
            800,
            300,
        ),
    ]
    for s in sessions:
        conn.execute(
            "INSERT INTO session "
            "(id, project_id, parent_id, slug, directory, title, version, "
            "time_created, time_updated, agent, model, cost, "
            "tokens_input, tokens_output) "
            "VALUES (?, 'proj_x', ?, ?, ?, ?, '0.1.0', ?, ?, ?, ?, ?, ?, ?)",
            (s[0], s[1], s[3].lower().replace(" ", "-"), s[2], s[3],
             s[4], s[5], s[6], s[7], s[8], s[9], s[10]),
        )

    # Messages: 2 in parent, 1 in child
    messages = [
        ("msg_1", "ses_parent_aaa", now_ms - 60_000, now_ms - 55_000,
         {"role": "user", "content": "Help me refactor"}),
        ("msg_2", "ses_parent_aaa", now_ms - 50_000, now_ms - 45_000,
         {"role": "assistant", "agent": "Sisyphus",
          "tokens": {"input": 100, "output": 50, "total": 150}}),
        ("msg_3", "ses_child_bbb", now_ms - 50_000, now_ms - 10_000,
         {"role": "assistant", "agent": "Sisyphus"}),
    ]
    for mid, sid, tc, tu, data in messages:
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, sid, tc, tu, json.dumps(data)),
        )

    # Parts: cover the 4 most important types
    parts = [
        # msg_1 (user) — single text
        ("prt_1a", "msg_1", "ses_parent_aaa", now_ms - 59_000,
         {"type": "text", "text": "Help me refactor src/foo.ts"}),
        # msg_2 (assistant) — full turn: step-start → reasoning → tool → text → step-finish
        ("prt_2a", "msg_2", "ses_parent_aaa", now_ms - 50_000,
         {"type": "step-start"}),
        ("prt_2b", "msg_2", "ses_parent_aaa", now_ms - 49_000,
         {"type": "reasoning", "text": "Need to read the file first..."}),
        ("prt_2c", "msg_2", "ses_parent_aaa", now_ms - 48_000,
         {"type": "tool", "tool": "Read", "state": {"status": "completed"},
          "input": {"path": "src/foo.ts"}}),
        ("prt_2d", "msg_2", "ses_parent_aaa", now_ms - 47_000,
         {"type": "text", "text": "I will refactor it as follows..."}),
        ("prt_2e", "msg_2", "ses_parent_aaa", now_ms - 46_000,
         {"type": "patch", "path": "src/foo.ts", "diff": "@@ ..."}),
        ("prt_2f", "msg_2", "ses_parent_aaa", now_ms - 45_000,
         {"type": "step-finish", "cost": 0.0042,
          "tokens": {"input": 100, "output": 50}}),
        # msg_3 (child session) — one text
        ("prt_3a", "msg_3", "ses_child_bbb", now_ms - 30_000,
         {"type": "text", "text": "Subtask response"}),
    ]
    for pid, mid, sid, tc, data in parts:
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pid, mid, sid, tc, tc, json.dumps(data)),
        )

    conn.commit()
    conn.close()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
