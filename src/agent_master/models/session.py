"""Session — \"a sustained working window\" attached to an Agent.

Per doc/01-data-model.md §2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_json, iso, load_json, parse_dt, row_get, utcnow
from .ids import new_id

SESSION_STATUSES: frozenset[str] = frozenset({"active", "idle", "closed"})
SessionStatus = str


@dataclass
class Session:
    agent_id: str
    workdir: str = ""
    id: str = field(default_factory=new_id)
    external_id: str | None = None
    parent_session_id: str | None = None
    started_at: datetime = field(default_factory=utcnow)
    ended_at: datetime | None = None
    last_active_at: datetime = field(default_factory=utcnow)
    status: SessionStatus = "active"
    summary: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Session:
        return cls(
            id=row["id"],
            agent_id=row["agent_id"],
            external_id=row_get(row, "external_id"),
            parent_session_id=row_get(row, "parent_session_id"),
            workdir=row_get(row, "workdir", "") or "",
            started_at=parse_dt(row["started_at"]) or utcnow(),
            ended_at=parse_dt(row_get(row, "ended_at")),
            last_active_at=parse_dt(row_get(row, "last_active_at")) or utcnow(),
            status=row_get(row, "status", "active"),
            summary=row_get(row, "summary"),
            meta=load_json(row_get(row, "meta")) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "external_id": self.external_id,
            "parent_session_id": self.parent_session_id,
            "workdir": self.workdir,
            "started_at": iso(self.started_at),
            "ended_at": iso(self.ended_at),
            "last_active_at": iso(self.last_active_at),
            "status": self.status,
            "summary": self.summary,
            "meta": self.meta,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "external_id": self.external_id,
            "parent_session_id": self.parent_session_id,
            "workdir": self.workdir,
            "started_at": iso(self.started_at),
            "ended_at": iso(self.ended_at),
            "last_active_at": iso(self.last_active_at),
            "status": self.status,
            "summary": self.summary,
            "meta": dump_json(self.meta),
        }
