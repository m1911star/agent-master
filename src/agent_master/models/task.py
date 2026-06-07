"""Task — a unit of work. Borrowed from paperclip's `issues`, drastically simplified.

Per doc/01-data-model.md §5.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_list, iso, load_list, parse_dt, row_get, utcnow
from .ids import new_id

TASK_STATUSES: frozenset[str] = frozenset({
    "pending",
    "in_progress",
    "blocked",
    "completed",
    "cancelled",
})

TaskStatus = str


@dataclass
class Task:
    title: str
    description: str = ""
    id: str = field(default_factory=new_id)
    parent_task_id: str | None = None
    assignee_agent_id: str | None = None
    status: TaskStatus = "pending"
    created_by: str = "user"  # "user" or "agent:<uuid>"
    priority: int = 50  # 0-100
    goal_chain: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Task:
        return cls(
            id=row["id"],
            title=row["title"],
            description=row_get(row, "description", "") or "",
            parent_task_id=row_get(row, "parent_task_id"),
            assignee_agent_id=row_get(row, "assignee_agent_id"),
            status=row_get(row, "status", "pending"),
            created_by=row_get(row, "created_by", "user"),
            priority=int(row_get(row, "priority", 50) or 50),
            goal_chain=load_list(row_get(row, "goal_chain")) or [],
            created_at=parse_dt(row["created_at"]) or utcnow(),
            started_at=parse_dt(row_get(row, "started_at")),
            completed_at=parse_dt(row_get(row, "completed_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "parent_task_id": self.parent_task_id,
            "assignee_agent_id": self.assignee_agent_id,
            "status": self.status,
            "created_by": self.created_by,
            "priority": self.priority,
            "goal_chain": list(self.goal_chain),
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "parent_task_id": self.parent_task_id,
            "assignee_agent_id": self.assignee_agent_id,
            "status": self.status,
            "created_by": self.created_by,
            "priority": self.priority,
            "goal_chain": dump_list(self.goal_chain),
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
        }
