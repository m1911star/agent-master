"""Generic repository base.

Each subclass declares:
    table:    str          — SQL table name
    model:    type          — dataclass with from_row + to_row
    pk:       str = "id"    — primary key column

Provides:
    create(obj)         — INSERT, returns obj
    get(pk)             — SELECT by primary key, returns model | None
    list(limit, offset) — SELECT * with optional pagination
    update(obj)         — UPDATE by primary key (replaces full row)
    delete(pk)          — DELETE by primary key
    exists(pk)          — SELECT 1 by primary key
    count()             — COUNT(*)

Subclasses may add table-specific queries.
"""

from __future__ import annotations

import sqlite3
from typing import Any, ClassVar, Protocol


class _ModelLike(Protocol):
    """Anything with from_row + to_row methods + an id-ish attribute."""

    def to_row(self) -> dict[str, Any]: ...


class Repo:
    """Base repository. Subclasses set `table`, `model`, optionally `pk`."""

    table: ClassVar[str] = ""
    model: ClassVar[type] = type(None)  # subclass overrides
    pk: ClassVar[str] = "id"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ── Generic CRUD ────────────────────────────────────────────────────

    def create(self, obj: Any) -> Any:
        row = obj.to_row()
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        sql = f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, tuple(row.values()))
        # AUTOINCREMENT tables (events): patch id back onto obj
        if row.get(self.pk) is None and cur.lastrowid is not None:
            setattr(obj, self.pk, cur.lastrowid)
        return obj

    def get(self, pk: Any) -> Any | None:
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {self.pk} = ?", (pk,)
        ).fetchone()
        if row is None:
            return None
        return self.model.from_row(row)

    def list(self, limit: int = 100, offset: int = 0) -> list[Any]:
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [self.model.from_row(r) for r in rows]

    def update(self, obj: Any) -> Any:
        row = obj.to_row()
        pk_val = row.pop(self.pk)
        sets = ", ".join(f"{k} = ?" for k in row)
        sql = f"UPDATE {self.table} SET {sets} WHERE {self.pk} = ?"
        self.conn.execute(sql, (*row.values(), pk_val))
        # restore pk on obj (we popped from local copy, not from obj)
        return obj

    def delete(self, pk: Any) -> None:
        self.conn.execute(f"DELETE FROM {self.table} WHERE {self.pk} = ?", (pk,))

    def exists(self, pk: Any) -> bool:
        row = self.conn.execute(
            f"SELECT 1 FROM {self.table} WHERE {self.pk} = ? LIMIT 1", (pk,)
        ).fetchone()
        return row is not None

    def count(self) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()
        return int(row[0])
