"""Artifact — concrete evidence that an agent finished something.

Per doc/01-data-model.md §7.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ._common import dump_json, iso, load_json, parse_dt, row_get, utcnow
from .ids import new_id

ARTIFACT_KINDS: frozenset[str] = frozenset({
    "file",
    "pr",
    "commit",
    "document",
    "screenshot",
    "url",
    "json",
})

ArtifactKind = str


@dataclass
class Artifact:
    run_id: str
    kind: ArtifactKind
    title: str
    id: str = field(default_factory=new_id)
    path: str | None = None
    content_hash: str | None = None
    size_bytes: int | None = None
    created_at: datetime = field(default_factory=utcnow)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Artifact:
        size = row_get(row, "size_bytes")
        return cls(
            id=row["id"],
            run_id=row["run_id"],
            kind=row_get(row, "kind", "file"),
            title=row_get(row, "title", "") or "",
            path=row_get(row, "path"),
            content_hash=row_get(row, "content_hash"),
            size_bytes=int(size) if size is not None else None,
            created_at=parse_dt(row["created_at"]) or utcnow(),
            meta=load_json(row_get(row, "meta")) or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind,
            "title": self.title,
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "created_at": iso(self.created_at),
            "meta": self.meta,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind,
            "title": self.title,
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "created_at": iso(self.created_at),
            "meta": dump_json(self.meta),
        }
