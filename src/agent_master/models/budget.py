"""Budget — token / dollar quota tracking.

Per doc/01-data-model.md §配套对象 (Budget block).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ._common import iso, parse_dt, row_get, utcnow
from .ids import new_id

BUDGET_SCOPES: frozenset[str] = frozenset({"agent", "task", "global"})
BUDGET_PERIODS: frozenset[str] = frozenset({"day", "month", "total"})
BUDGET_ON_EXCEED: frozenset[str] = frozenset({"pause", "warn", "stop"})

BudgetScope = str
BudgetPeriod = str
BudgetOnExceed = str


def _dec(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))


@dataclass
class Budget:
    scope: BudgetScope
    period: BudgetPeriod
    id: str = field(default_factory=new_id)
    limit_tokens: int | None = None
    limit_usd: Decimal | None = None
    spent_tokens: int = 0
    spent_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    on_exceed: BudgetOnExceed = "warn"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Budget:
        limit_tokens = row_get(row, "limit_tokens")
        limit_usd = row_get(row, "limit_usd")
        return cls(
            id=row["id"],
            scope=row_get(row, "scope", "agent"),
            period=row_get(row, "period", "month"),
            limit_tokens=int(limit_tokens) if limit_tokens is not None else None,
            limit_usd=_dec(limit_usd) if limit_usd is not None else None,
            spent_tokens=int(row_get(row, "spent_tokens", 0) or 0),
            spent_usd=_dec(row_get(row, "spent_usd", 0)),
            on_exceed=row_get(row, "on_exceed", "warn"),
            created_at=parse_dt(row["created_at"]) or utcnow(),
            updated_at=parse_dt(row["updated_at"]) or utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "period": self.period,
            "limit_tokens": self.limit_tokens,
            "limit_usd": str(self.limit_usd) if self.limit_usd is not None else None,
            "spent_tokens": self.spent_tokens,
            "spent_usd": str(self.spent_usd),
            "on_exceed": self.on_exceed,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "period": self.period,
            "limit_tokens": self.limit_tokens,
            "limit_usd": str(self.limit_usd) if self.limit_usd is not None else None,
            "spent_tokens": self.spent_tokens,
            "spent_usd": str(self.spent_usd),
            "on_exceed": self.on_exceed,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }
