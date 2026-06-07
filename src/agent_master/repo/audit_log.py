"""AuditLogRepo — append-only audit trail."""

from __future__ import annotations

from ..models import AuditLog
from .base import Repo


class AuditLogRepo(Repo):
    table = "audit_log"
    model = AuditLog

    def list_recent(self, limit: int = 100) -> list[AuditLog]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [AuditLog.from_row(r) for r in rows]

    def list_for_target(self, target_type: str, target_id: str) -> list[AuditLog]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE target_type = ? AND target_id = ? "
            "ORDER BY ts DESC",
            (target_type, target_id),
        ).fetchall()
        return [AuditLog.from_row(r) for r in rows]
