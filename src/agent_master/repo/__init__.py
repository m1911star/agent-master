"""Hand-rolled repositories — no ORM.

Each repository wraps one table. Generic CRUD operations live on the base;
table-specific queries (e.g. SessionRepo.list_active) go on subclasses as
they're needed (M1.3 onward, when adapters have actual data to read back).

Convention: repositories take a sqlite3.Connection by argument, never own
one. The caller manages connection lifecycle. This makes them trivially
testable with a tmp_path connection.
"""

from __future__ import annotations

from .agent import AgentRepo
from .approval import ApprovalRepo
from .artifact import ArtifactRepo
from .audit_log import AuditLogRepo
from .base import Repo
from .budget import BudgetRepo
from .event import EventRepo
from .rule import RuleRepo
from .run import RunRepo
from .session import SessionRepo
from .task import TaskRepo
from .workflow import WorkflowRepo

__all__ = [
    "AgentRepo",
    "ApprovalRepo",
    "ArtifactRepo",
    "AuditLogRepo",
    "BudgetRepo",
    "EventRepo",
    "Repo",
    "RuleRepo",
    "RunRepo",
    "SessionRepo",
    "TaskRepo",
    "WorkflowRepo",
]
