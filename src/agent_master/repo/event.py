"""EventRepo — event CRUD + ordered streaming.

The events table is the high-frequency one. id is AUTOINCREMENT, so create()
patches the assigned id onto the dataclass.
"""

from __future__ import annotations

from ..models import Event
from .base import Repo


class EventRepo(Repo):
    table = "events"
    model = Event

    def list_by_run(
        self,
        run_id: str,
        kind: str | None = None,
        after_seq: int | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        clauses = ["run_id = ?"]
        params: list = [run_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        sql = (
            "SELECT * FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY seq LIMIT ?"
        )
        params.append(limit)
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [Event.from_row(r) for r in rows]

    def max_seq(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(seq) FROM events WHERE run_id = ?", (run_id,)
        ).fetchone()
        val = row[0] if row else None
        return int(val) if val is not None else -1
