"""TaskRepo — task CRUD + tree walking + assignee queries."""

from __future__ import annotations

from ..models import Task
from .base import Repo


class TaskRepo(Repo):
    table = "tasks"
    model = Task

    def list_by_status(self, status: str) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = ?", (status,)
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def list_children(self, parent_task_id: str) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ?", (parent_task_id,)
        ).fetchall()
        return [Task.from_row(r) for r in rows]
