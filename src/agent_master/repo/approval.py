"""ApprovalRepo — pending queue + decision lookup."""

from __future__ import annotations

from ..models import Approval
from .base import Repo


class ApprovalRepo(Repo):
    table = "approvals"
    model = Approval

    def list_pending(self, limit: int = 100) -> list[Approval]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' "
            "ORDER BY requested_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Approval.from_row(r) for r in rows]
