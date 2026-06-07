"""Approval — a gate waiting on a human decision.

Per doc/01-data-model.md §6.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_json, iso, load_json, parse_dt, row_get, utcnow
from .ids import new_id

APPROVAL_STATUSES: frozenset[str] = frozenset({
    "pending",
    "approved",
    "rejected",
    "expired",
    "auto_approved",
})
APPROVAL_DEFAULT_ACTIONS: frozenset[str] = frozenset({"reject", "approve", "pause"})

ApprovalStatus = str
ApprovalDefaultAction = str


@dataclass
class Approval:
    run_id: str
    agent_id: str
    subject: str
    id: str = field(default_factory=new_id)
    requested_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    status: ApprovalStatus = "pending"
    default_action: ApprovalDefaultAction = "reject"
    detail: dict[str, Any] = field(default_factory=dict)
    decision_by: str | None = None
    decision_reason: str | None = None
    rule_created_id: str | None = None
    checkpoint_data: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Approval:
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            agent_id=row["agent_id"],
            requested_at=parse_dt(row["requested_at"]) or utcnow(),
            decided_at=parse_dt(row_get(row, "decided_at")),
            expires_at=parse_dt(row_get(row, "expires_at")),
            status=row_get(row, "status", "pending"),
            default_action=row_get(row, "default_action", "reject"),
            subject=row_get(row, "subject", "") or "",
            detail=load_json(row_get(row, "detail")) or {},
            decision_by=row_get(row, "decision_by"),
            decision_reason=row_get(row, "decision_reason"),
            rule_created_id=row_get(row, "rule_created_id"),
            checkpoint_data=load_json(row_get(row, "checkpoint_data")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "requested_at": iso(self.requested_at),
            "decided_at": iso(self.decided_at),
            "expires_at": iso(self.expires_at),
            "status": self.status,
            "default_action": self.default_action,
            "subject": self.subject,
            "detail": self.detail,
            "decision_by": self.decision_by,
            "decision_reason": self.decision_reason,
            "rule_created_id": self.rule_created_id,
            "checkpoint_data": self.checkpoint_data,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "requested_at": iso(self.requested_at),
            "decided_at": iso(self.decided_at),
            "expires_at": iso(self.expires_at),
            "status": self.status,
            "default_action": self.default_action,
            "subject": self.subject,
            "detail": dump_json(self.detail),
            "decision_by": self.decision_by,
            "decision_reason": self.decision_reason,
            "rule_created_id": self.rule_created_id,
            "checkpoint_data": dump_json(self.checkpoint_data),
        }
