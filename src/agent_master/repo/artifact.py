"""ArtifactRepo — artifact CRUD + run lookup."""

from __future__ import annotations

from ..models import Artifact
from .base import Repo


class ArtifactRepo(Repo):
    table = "artifacts"
    model = Artifact

    def list_by_run(self, run_id: str) -> list[Artifact]:
        rows = self.conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [Artifact.from_row(r) for r in rows]
