"""SessionRepo — session CRUD + topology helpers."""

from __future__ import annotations

from ..models import Session
from .base import Repo


class SessionRepo(Repo):
    table = "sessions"
    model = Session

    def list_active(self, agent_id: str | None = None, limit: int = 100) -> list[Session]:
        if agent_id is None:
            sql = (
                "SELECT * FROM sessions WHERE status = 'active' "
                "ORDER BY last_active_at DESC LIMIT ?"
            )
            rows = self.conn.execute(sql, (limit,)).fetchall()
        else:
            sql = (
                "SELECT * FROM sessions WHERE agent_id = ? AND status = 'active' "
                "ORDER BY last_active_at DESC LIMIT ?"
            )
            rows = self.conn.execute(sql, (agent_id, limit)).fetchall()
        return [Session.from_row(r) for r in rows]

    def get_by_external_id(self, external_id: str) -> Session | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE external_id = ?", (external_id,)
        ).fetchone()
        return Session.from_row(row) if row else None

    def list_children(self, parent_session_id: str) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM sessions WHERE parent_session_id = ?", (parent_session_id,)
        ).fetchall()
        return [Session.from_row(r) for r in rows]
