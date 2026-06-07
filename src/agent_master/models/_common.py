"""Shared helpers used by every model module.

Tiny on purpose — these are leaf utilities, no business logic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def utcnow() -> datetime:
    """Timezone-aware UTC now. We never store naive datetimes."""
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """Format a datetime as ISO8601 with explicit 'Z'. None passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    """Inverse of `iso`. Accepts ISO strings, datetimes, or None.

    SQLite gives us strings back (TEXT column) so this is the hot path.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    s = str(value)
    # tolerate "Z" suffix that fromisoformat() can't handle pre-3.11... we're 3.12+
    # so it works, but keep the cleanup for paranoia.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def dump_json(value: Any) -> str | None:
    """Serialize dicts/lists for jsonb-style columns. None passes through."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def load_json(value: Any) -> Any:
    """Inverse of dump_json. None/empty -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def dump_list(value: list[str] | None) -> str | None:
    """text[] columns are stored as JSON arrays in SQLite TEXT."""
    if value is None:
        return None
    return json.dumps(list(value), ensure_ascii=False)


def load_list(value: Any) -> list[str] | None:
    """Inverse of dump_list. None/'' -> None, else parsed JSON array."""
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return list(value)
    return list(json.loads(value))


def row_get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    """sqlite3.Row supports __getitem__ but not .get(); normalize that."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return default
