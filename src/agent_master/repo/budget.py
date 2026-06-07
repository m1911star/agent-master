"""BudgetRepo — budget CRUD."""

from __future__ import annotations

from ..models import Budget
from .base import Repo


class BudgetRepo(Repo):
    table = "budgets"
    model = Budget

    def list_by_scope(self, scope: str) -> list[Budget]:
        rows = self.conn.execute(
            "SELECT * FROM budgets WHERE scope = ?", (scope,)
        ).fetchall()
        return [Budget.from_row(r) for r in rows]
