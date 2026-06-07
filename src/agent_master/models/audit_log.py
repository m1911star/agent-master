"""AuditLog — every mutation leaves a trace.

Per doc/01-data-model.md §配套对象 (AuditLog block) and §与 paperclip 表对应总表
(maps to paperclip's `activity_log`). Persisted for compliance/debug; not on the
hot path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_json, iso, load_json, parse_dt, row_get, utcnow
from .ids import new_id


@dataclass
class AuditLog:
    actor: str
    action: str
    target_type: str
    target_id: str
    id: str = field(default_factory=new_id)
    ts: datetime = field(default_factory=utcnow)
    payload: dict[str, Any] | None = None
    ip_addr: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> AuditLog:
        return cls(
            id=row["id"],
            ts=parse_dt(row["ts"]) or utcnow(),
            actor=row_get(row, "actor", "") or "",
            action=row_get(row, "action", "") or "",
            target_type=row_get(row, "target_type", "") or "",
            target_id=row_get(row, "target_id", "") or "",
            payload=load_json(row_get(row, "payload")),
            ip_addr=row_get(row, "ip_addr"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": iso(self.ts),
            "actor": self.actor,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "payload": self.payload,
            "ip_addr": self.ip_addr,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": iso(self.ts),
            "actor": self.actor,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "payload": dump_json(self.payload),
            "ip_addr": self.ip_addr,
        }
