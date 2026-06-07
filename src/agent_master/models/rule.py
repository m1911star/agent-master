"""Rule — pattern-based allow/deny/ask (V0.4 HITL).

Per doc/01-data-model.md §配套对象 (Rule block).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import iso, parse_dt, row_get, utcnow
from .ids import new_id

RULE_ACTIONS: frozenset[str] = frozenset({"allow", "deny", "ask"})
RULE_SCOPES: frozenset[str] = frozenset({"session", "permanent"})

RuleAction = str
RuleScope = str


@dataclass
class Rule:
    pattern: str
    action: RuleAction
    id: str = field(default_factory=new_id)
    agent_id: str | None = None
    scope: RuleScope = "permanent"
    expires_at: datetime | None = None
    created_from_approval_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Rule:
        return cls(
            id=row["id"],
            agent_id=row_get(row, "agent_id"),
            pattern=row_get(row, "pattern", "") or "",
            action=row_get(row, "action", "ask"),
            scope=row_get(row, "scope", "permanent"),
            expires_at=parse_dt(row_get(row, "expires_at")),
            created_from_approval_id=row_get(row, "created_from_approval_id"),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "pattern": self.pattern,
            "action": self.action,
            "scope": self.scope,
            "expires_at": iso(self.expires_at),
            "created_from_approval_id": self.created_from_approval_id,
            "created_at": iso(self.created_at),
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "pattern": self.pattern,
            "action": self.action,
            "scope": self.scope,
            "expires_at": iso(self.expires_at),
            "created_from_approval_id": self.created_from_approval_id,
            "created_at": iso(self.created_at),
        }
