"""Build a minimal hermes-shape SQLite fixture for adapter tests."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "fixture.db"


SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT,
    billing_base_url TEXT
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT
);
"""


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.executescript(SCHEMA)

    now = time.time()

    sessions = [
        ("hms_parent", "cli", None, now - 60, now - 30,
         "claude-opus-4.7", 4, 1, 5000, 1500),
        ("hms_child", "cli", "hms_parent", now - 50, now - 10,
         "claude-haiku-4.5", 2, 0, 800, 400),
    ]
    for s in sessions:
        conn.execute(
            "INSERT INTO sessions (id, source, parent_session_id, started_at, "
            "ended_at, model, message_count, tool_call_count, "
            "input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            s,
        )

    messages = [
        # parent session: user → assistant w/ reasoning + tool_call → tool result → assistant
        (1, "hms_parent", "user", "Refactor this code", None, None, None, now - 59, None, None, None),
        (2, "hms_parent", "assistant", "I will check the file first.",
         None, json.dumps([{"id": "call_1", "function": {"name": "Read", "arguments": "{}"}}]),
         None, now - 55, 100, "tool_calls", "Need to read first."),
        (3, "hms_parent", "tool", "file contents...",
         "call_1", None, "Read", now - 50, 200, None, None),
        (4, "hms_parent", "assistant", "Here is the refactor.",
         None, None, None, now - 45, 80, "stop", None),
        # child session: simple exchange
        (5, "hms_child", "user", "subtask", None, None, None, now - 50, None, None, None),
        (6, "hms_child", "assistant", "subtask response",
         None, None, None, now - 30, 50, "stop", None),
    ]
    for m in messages:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_call_id, "
            "tool_calls, tool_name, timestamp, token_count, finish_reason, reasoning) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            m,
        )

    conn.commit()
    conn.close()
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
