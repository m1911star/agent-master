"""RunRepo — run CRUD + session/status queries."""

from __future__ import annotations

from ..models import Run
from .base import Repo


class RunRepo(Repo):
    table = "runs"
    model = Run

    def list_by_session(self, session_id: str) -> list[Run]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at", (session_id,)
        ).fetchall()
        return [Run.from_row(r) for r in rows]
