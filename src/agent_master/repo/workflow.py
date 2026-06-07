"""WorkflowRepo — workflow CRUD."""

from __future__ import annotations

from ..models import Workflow
from .base import Repo


class WorkflowRepo(Repo):
    table = "workflows"
    model = Workflow

    def get_by_name(self, name: str) -> Workflow | None:
        row = self.conn.execute(
            "SELECT * FROM workflows WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        return Workflow.from_row(row) if row else None
