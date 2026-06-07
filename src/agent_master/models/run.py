"""Run — a single execution batch within a Session.

Per doc/01-data-model.md §3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ._common import iso, parse_dt, row_get, utcnow
from .ids import new_id

RUN_TRIGGERS: frozenset[str] = frozenset({"manual", "heartbeat", "cron", "webhook", "spawn"})
RUN_STATUSES: frozenset[str] = frozenset({"pending", "running", "success", "failed", "interrupted"})
RUN_EXIT_REASONS: frozenset[str] = frozenset({
    "completed",
    "error",
    "approval_pending",
    "budget_exceeded",
    "user_cancelled",
})

RunTrigger = str
RunStatus = str
RunExitReason = str


def _to_decimal(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))


@dataclass
class Run:
    session_id: str
    id: str = field(default_factory=new_id)
    task_id: str | None = None
    trigger: RunTrigger = "manual"
    started_at: datetime = field(default_factory=utcnow)
    ended_at: datetime | None = None
    status: RunStatus = "pending"
    exit_reason: RunExitReason | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    summary: str | None = None
    error_message: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Run:
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            task_id=row_get(row, "task_id"),
            trigger=row_get(row, "trigger", "manual"),
            started_at=parse_dt(row["started_at"]) or utcnow(),
            ended_at=parse_dt(row_get(row, "ended_at")),
            status=row_get(row, "status", "pending"),
            exit_reason=row_get(row, "exit_reason"),
            tokens_in=int(row_get(row, "tokens_in", 0) or 0),
            tokens_out=int(row_get(row, "tokens_out", 0) or 0),
            cost_usd=_to_decimal(row_get(row, "cost_usd", 0)),
            summary=row_get(row, "summary"),
            error_message=row_get(row, "error_message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "trigger": self.trigger,
            "started_at": iso(self.started_at),
            "ended_at": iso(self.ended_at),
            "status": self.status,
            "exit_reason": self.exit_reason,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": str(self.cost_usd),
            "summary": self.summary,
            "error_message": self.error_message,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "trigger": self.trigger,
            "started_at": iso(self.started_at),
            "ended_at": iso(self.ended_at),
            "status": self.status,
            "exit_reason": self.exit_reason,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": str(self.cost_usd),
            "summary": self.summary,
            "error_message": self.error_message,
        }
