"""Domain dataclasses for the 7 core + 4 satellite objects.

One file per object, mirroring doc/01-data-model.md verbatim. Each module
exposes:
    * a frozen-but-not-strictly-frozen dataclass (mutable on purpose; repo
      layer mutates in place when updating fields like updated_at)
    * `from_row(sqlite3.Row | Mapping) -> T`   reads a DB row
    * `to_dict() -> dict`                       JSON-friendly serialization

No ORM. No declarative metaclasses. Just dataclasses + helpers.
"""

from __future__ import annotations

from .agent import Agent, AgentStatus
from .approval import Approval, ApprovalDefaultAction, ApprovalStatus
from .artifact import Artifact, ArtifactKind
from .audit_log import AuditLog
from .budget import Budget, BudgetOnExceed, BudgetPeriod, BudgetScope
from .event import Event, EventKind
from .ids import new_id
from .run import Run, RunExitReason, RunStatus, RunTrigger
from .rule import Rule, RuleAction, RuleScope
from .session import Session, SessionStatus
from .task import Task, TaskStatus
from .workflow import Workflow

__all__ = [
    "Agent",
    "AgentStatus",
    "Approval",
    "ApprovalDefaultAction",
    "ApprovalStatus",
    "Artifact",
    "ArtifactKind",
    "AuditLog",
    "Budget",
    "BudgetOnExceed",
    "BudgetPeriod",
    "BudgetScope",
    "Event",
    "EventKind",
    "Run",
    "RunExitReason",
    "RunStatus",
    "RunTrigger",
    "Rule",
    "RuleAction",
    "RuleScope",
    "Session",
    "SessionStatus",
    "Task",
    "TaskStatus",
    "Workflow",
    "new_id",
]
