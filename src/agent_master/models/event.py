"""Event — a single atomic event in a Run's stream.

Per doc/01-data-model.md §4.

Note the id type: doc says bigserial (high-frequency monotonically-increasing
primary key) — in SQLite that maps to INTEGER PRIMARY KEY AUTOINCREMENT.
We keep it as int (None until first insert).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_json, iso, load_json, parse_dt, row_get, utcnow

# Standard kind enum — locked at V0.1 per doc/01 §4.
EVENT_KINDS: frozenset[str] = frozenset({
    "user_message",
    "assistant_message",
    "reasoning",
    "tool_call",
    "tool_result",
    "status_change",
    "approval_requested",
    "approval_decided",
    "artifact_created",
    "error",
    "session_start",
    "session_end",
    "run_start",
    "run_end",
    "raw",  # fallback when an adapter reports a non-standard event
})

EVENT_LEVELS: frozenset[str] = frozenset({"info", "warn", "error"})

EventKind = str


@dataclass
class Event:
    run_id: str
    seq: int
    kind: EventKind
    id: int | None = None  # assigned by DB on insert
    ts: datetime = field(default_factory=utcnow)
    created_at: datetime = field(default_factory=utcnow)
    stream: str | None = None
    level: str | None = None
    color: str | None = None
    text: str | None = None
    payload: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Event:
        return cls(
            id=int(row["id"]) if row_get(row, "id") is not None else None,
            run_id=row["run_id"],
            seq=int(row["seq"]),
            ts=parse_dt(row["ts"]) or utcnow(),
            created_at=parse_dt(row["created_at"]) or utcnow(),
            kind=row_get(row, "kind", "raw"),
            stream=row_get(row, "stream"),
            level=row_get(row, "level"),
            color=row_get(row, "color"),
            text=row_get(row, "text"),
            payload=load_json(row_get(row, "payload")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": iso(self.ts),
            "created_at": iso(self.created_at),
            "kind": self.kind,
            "stream": self.stream,
            "level": self.level,
            "color": self.color,
            "text": self.text,
            "payload": self.payload,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": iso(self.ts),
            "created_at": iso(self.created_at),
            "kind": self.kind,
            "stream": self.stream,
            "level": self.level,
            "color": self.color,
            "text": self.text,
            "payload": dump_json(self.payload),
        }
