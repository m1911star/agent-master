"""AgentRepo — agent CRUD + status filtering."""

from __future__ import annotations

from ..models import Agent
from .base import Repo


class AgentRepo(Repo):
    table = "agents"
    model = Agent

    def list_by_status(self, status: str) -> list[Agent]:
        rows = self.conn.execute(
            "SELECT * FROM agents WHERE status = ?", (status,)
        ).fetchall()
        return [Agent.from_row(r) for r in rows]
