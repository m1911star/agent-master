"""SQLite WAL-friendly polling tailer.

Used when an external SQLite DB (Hermes, OpenCode, omp) is being written
by another process and we want to read new rows incrementally. Opens
read-only, polls by primary key.

Per doc/03-realtime.md §SQLite WAL tail.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class SqliteTailer:
    """Polls a SQLite table for new rows ordered by an integer/text key.

    Tracks the last-seen value of a monotonic column (default 'id') and
    SELECTs only rows past that mark on each poll(). Caller decides poll
    cadence (typically 200ms — see doc/03 perf budget).
    """

    def __init__(
        self,
        db_path: Path,
        table: str,
        *,
        cursor_col: str = "id",
        order_col: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.table = table
        self.cursor_col = cursor_col
        # Default to ordering by the cursor column itself.
        self.order_col = order_col or cursor_col
        self._conn: sqlite3.Connection | None = None
        self.last_seen: Any = None

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            # Read-only open via URI; will not corrupt the writer.
            uri = f"file:{self.db_path}?mode=ro&immutable=0"
            self._conn = sqlite3.connect(uri, uri=True, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def seed(self) -> None:
        """Set last_seen to the current MAX so we only read truly-new rows."""
        conn = self._ensure_open()
        row = conn.execute(
            f"SELECT MAX({self.cursor_col}) FROM {self.table}"
        ).fetchone()
        self.last_seen = row[0] if row else None

    def poll(self, limit: int = 1000) -> list[sqlite3.Row]:
        """Return rows where cursor_col > last_seen, in order_col order."""
        conn = self._ensure_open()
        if self.last_seen is None:
            sql = (
                f"SELECT * FROM {self.table} ORDER BY {self.order_col} LIMIT ?"
            )
            rows = conn.execute(sql, (limit,)).fetchall()
        else:
            sql = (
                f"SELECT * FROM {self.table} WHERE {self.cursor_col} > ? "
                f"ORDER BY {self.order_col} LIMIT ?"
            )
            rows = conn.execute(sql, (self.last_seen, limit)).fetchall()
        if rows:
            # Update cursor to the max we just saw.
            self.last_seen = max(r[self.cursor_col] for r in rows)
        return rows

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SqliteTailer:
        self._ensure_open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
