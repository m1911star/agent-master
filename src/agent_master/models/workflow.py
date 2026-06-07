"""Workflow — DAG template (V0.3 surface).

Per doc/01-data-model.md §配套对象 (Workflow block).
Schema is intentionally tiny — V0.3 expands it; we just stake out the table now.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import iso, parse_dt, row_get, utcnow
from .ids import new_id


@dataclass
class Workflow:
    name: str
    definition_yaml: str
    id: str = field(default_factory=new_id)
    version: int = 1
    created_at: datetime = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Workflow:
        return cls(
            id=row["id"],
            name=row_get(row, "name", "") or "",
            definition_yaml=row_get(row, "definition_yaml", "") or "",
            version=int(row_get(row, "version", 1) or 1),
            created_at=parse_dt(row["created_at"]) or utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "definition_yaml": self.definition_yaml,
            "version": self.version,
            "created_at": iso(self.created_at),
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "definition_yaml": self.definition_yaml,
            "version": self.version,
            "created_at": iso(self.created_at),
        }
