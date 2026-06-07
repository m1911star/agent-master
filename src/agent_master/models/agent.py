"""Agent — \"a thing that can do work\".

Per doc/01-data-model.md §1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import (
    dump_json,
    dump_list,
    iso,
    load_json,
    load_list,
    parse_dt,
    row_get,
    utcnow,
)
from .ids import new_id

# Status machine: idle -> busy -> idle | error; * -> paused | offline.
AGENT_STATUSES: frozenset[str] = frozenset({"idle", "busy", "paused", "error", "offline"})
AgentStatus = str  # kept as str + app-layer validation per doc/01 §命名约定


@dataclass
class Agent:
    name: str
    adapter_type: str
    id: str = field(default_factory=new_id)
    adapter_config: dict[str, Any] = field(default_factory=dict)
    status: AgentStatus = "idle"
    capabilities: list[str] = field(default_factory=list)
    budget_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    # --- helpers ---------------------------------------------------------

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Agent:
        return cls(
            id=row["id"],
            name=row["name"],
            adapter_type=row["adapter_type"],
            adapter_config=load_json(row_get(row, "adapter_config")) or {},
            status=row_get(row, "status", "idle"),
            capabilities=load_list(row_get(row, "capabilities")) or [],
            budget_id=row_get(row, "budget_id"),
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "adapter_type": self.adapter_type,
            "adapter_config": self.adapter_config,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "budget_id": self.budget_id,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }

    def to_row(self) -> dict[str, Any]:
        """Tuple of values ready for INSERT/UPDATE — JSON-encoded where needed."""
        return {
            "id": self.id,
            "name": self.name,
            "adapter_type": self.adapter_type,
            "adapter_config": dump_json(self.adapter_config),
            "status": self.status,
            "capabilities": dump_list(self.capabilities),
            "budget_id": self.budget_id,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
