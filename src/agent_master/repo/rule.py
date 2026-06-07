"""RuleRepo — pattern rule CRUD + lookup."""

from __future__ import annotations

from ..models import Rule
from .base import Repo


class RuleRepo(Repo):
    table = "rules"
    model = Rule

    def list_by_action(self, action: str) -> list[Rule]:
        rows = self.conn.execute(
            "SELECT * FROM rules WHERE action = ? ORDER BY created_at DESC",
            (action,),
        ).fetchall()
        return [Rule.from_row(r) for r in rows]

    def list_for_agent(self, agent_id: str | None) -> list[Rule]:
        """Includes both agent-scoped and global (agent_id IS NULL) rules."""
        if agent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM rules WHERE agent_id IS NULL"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM rules WHERE agent_id = ? OR agent_id IS NULL",
                (agent_id,),
            ).fetchall()
        return [Rule.from_row(r) for r in rows]
