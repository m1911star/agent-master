"""OpenCode Observer adapter.

Maps OpenCode's session/message/part SQLite schema into our standard objects.

OpenCode's structure (verified against ~/.local/share/opencode/opencode.db):

    session(id, parent_id, agent, model, cost, tokens_*, time_created, time_updated, ...)
      ↓ 1:N
    message(id, session_id, time_created, time_updated, data:json)
      ↓ 1:N
    part(id, message_id, session_id, time_created, data:json)

The `data` JSON column on `part` is the actual content. Its `type` field
determines the kind of event:

    text         → assistant_message (or user_message via message.data.role)
    reasoning    → reasoning
    tool         → tool_call (state in data.state.status)
    step-start   → run_start
    step-finish  → run_end (carries final tokens + cost)
    patch        → artifact_created (file edit)
    file         → artifact_created (file attach)
    compaction   → status_change
    subtask      → status_change
    *            → raw fallback

We do not read `event` table — in this DB it stays empty; the canonical
stream lives in `part`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_master.adapters.base import (
    Observer,
    SessionDescriptor,
    Subscription,
)
from agent_master.models import Event, Run, Session
from agent_master.watch.sqlite_tail import SqliteTailer


def _ms_to_dt(ms: int | None) -> datetime:
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


# Map opencode part types to our standard Event.kind.
# Anything not listed falls back to "raw".
PART_TYPE_TO_KIND: dict[str, str] = {
    "text": "assistant_message",
    "reasoning": "reasoning",
    "tool": "tool_call",
    "step-start": "run_start",
    "step-finish": "run_end",
    "patch": "artifact_created",
    "file": "artifact_created",
    "compaction": "status_change",
    "subtask": "status_change",
}


def _summarize_part(part_data: dict[str, Any]) -> str:
    """Human-readable one-liner for the UI."""
    t = part_data.get("type", "")
    if t == "text":
        return (part_data.get("text") or "")[:200]
    if t == "reasoning":
        return (part_data.get("text") or part_data.get("reasoning") or "")[:200]
    if t == "tool":
        tool_name = part_data.get("tool") or part_data.get("name") or "tool"
        state = part_data.get("state", {})
        status = state.get("status", "")
        return f"{tool_name}({status})" if status else tool_name
    if t == "patch":
        return f"patch: {part_data.get('path', '')}"
    if t == "step-finish":
        cost = part_data.get("cost")
        return f"step-finish (cost={cost})" if cost is not None else "step-finish"
    if t == "step-start":
        return "step-start"
    return t


class OpenCodeObserver(Observer):
    """Read-only Observer for OpenCode's SQLite store.

    We map OpenCode session+message+part rows to our Session+Run+Event
    triplet:
      - opencode.session ↔ our Session (1:1, with parent_id topology)
      - opencode.message ↔ a synthetic Run (one Run per message turn)
      - opencode.part    ↔ our Event (with `seq` ordered by time_created)

    For the simplified V0.1 mapping we treat each `message` as a Run and
    its parts (in time_created order) as Events. The Run.task_id stays
    null — task association is V0.2's job.
    """

    name = "opencode"

    def __init__(
        self,
        db_path: Path,
        *,
        recent_hours: int = 24,
        poll_interval_ms: int = 200,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.recent_hours = recent_hours
        self.poll_interval_ms = poll_interval_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── helpers ─────────────────────────────────────────────────────────

    def _readonly_conn(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Observer protocol ───────────────────────────────────────────────

    def list_existing_sessions(self) -> list[SessionDescriptor]:
        if not self.db_path.exists():
            return []

        cutoff_ms = int(
            (time.time() - self.recent_hours * 3600) * 1000
        )
        conn = self._readonly_conn()
        try:
            rows = conn.execute(
                "SELECT id, parent_id, directory, title, time_created, time_updated, "
                "agent, model, cost, tokens_input, tokens_output "
                "FROM session WHERE time_updated >= ? ORDER BY time_updated DESC",
                (cutoff_ms,),
            ).fetchall()
        finally:
            conn.close()

        descriptors: list[SessionDescriptor] = []
        for r in rows:
            descriptors.append(
                SessionDescriptor(
                    external_id=r["id"],
                    workdir=r["directory"] or "",
                    last_active_ts=(r["time_updated"] / 1000.0)
                    if r["time_updated"]
                    else None,
                    meta={
                        "title": r["title"],
                        "parent_id": r["parent_id"],
                        "agent": r["agent"],
                        "model": r["model"],
                        "cost": r["cost"],
                        "tokens_input": r["tokens_input"],
                        "tokens_output": r["tokens_output"],
                    },
                )
            )
        return descriptors

    def parse_session(
        self, descriptor: SessionDescriptor
    ) -> tuple[Session, list[Run], list[Event]]:
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

        conn = self._readonly_conn()
        try:
            srow = conn.execute(
                "SELECT * FROM session WHERE id = ?", (descriptor.external_id,)
            ).fetchone()
            if srow is None:
                raise ValueError(f"session {descriptor.external_id} not found")

            session = Session(
                # Our id is internal — caller gets to assign
                agent_id="",  # filled in by core when registering
                external_id=srow["id"],
                parent_session_id=None,  # core resolves opencode parent_id → our id
                workdir=srow["directory"] or "",
                started_at=_ms_to_dt(srow["time_created"]),
                last_active_at=_ms_to_dt(srow["time_updated"]),
                ended_at=_ms_to_dt(srow["time_archived"])
                if srow["time_archived"]
                else None,
                status="closed" if srow["time_archived"] else "active",
                summary=srow["title"],
                meta={
                    "opencode_parent_id": srow["parent_id"],
                    "agent": srow["agent"],
                    "model": srow["model"],
                    "cost": srow["cost"],
                    "tokens_input": srow["tokens_input"],
                    "tokens_output": srow["tokens_output"],
                    "tokens_reasoning": srow["tokens_reasoning"],
                    "tokens_cache_read": srow["tokens_cache_read"],
                    "tokens_cache_write": srow["tokens_cache_write"],
                },
            )

            mrows = conn.execute(
                "SELECT id, time_created, time_updated, data FROM message "
                "WHERE session_id = ? ORDER BY time_created, id",
                (srow["id"],),
            ).fetchall()

            runs: list[Run] = []
            events: list[Event] = []
            for mrow in mrows:
                try:
                    msg_data = json.loads(mrow["data"])
                except json.JSONDecodeError:
                    msg_data = {}

                run = Run(
                    session_id=session.id,
                    trigger="manual",
                    started_at=_ms_to_dt(mrow["time_created"]),
                    ended_at=_ms_to_dt(mrow["time_updated"])
                    if mrow["time_updated"]
                    else None,
                    status="success",  # opencode doesn't track per-turn failure
                    summary=msg_data.get("role"),
                )
                runs.append(run)

                # Parts within this message become events
                prows = conn.execute(
                    "SELECT id, time_created, data FROM part "
                    "WHERE message_id = ? ORDER BY time_created, id",
                    (mrow["id"],),
                ).fetchall()

                for seq, prow in enumerate(prows):
                    try:
                        part_data = json.loads(prow["data"])
                    except json.JSONDecodeError:
                        part_data = {}

                    ptype = part_data.get("type", "")
                    kind = PART_TYPE_TO_KIND.get(ptype, "raw")

                    events.append(
                        Event(
                            run_id=run.id,
                            seq=seq,
                            ts=_ms_to_dt(prow["time_created"]),
                            kind=kind,
                            text=_summarize_part(part_data),
                            payload=part_data,
                        )
                    )
        finally:
            conn.close()

        return session, runs, events

    def subscribe(self, callback: Callable[[Event], None]) -> Subscription:
        """Tail the part table; emit Events as new rows arrive.

        For V0.1 we emit raw Events without joining to message/run — the
        ingest pipeline is responsible for resolving message_id → run_id
        based on what it has hydrated.
        """
        tailer = SqliteTailer(
            self.db_path,
            "part",
            cursor_col="time_created",
            order_col="time_created",
        )
        # Seed so we don't replay history; only new rows.
        tailer.seed()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    rows = tailer.poll()
                    for row in rows:
                        try:
                            part_data = json.loads(row["data"])
                        except (json.JSONDecodeError, TypeError):
                            part_data = {}
                        ptype = part_data.get("type", "")
                        kind = PART_TYPE_TO_KIND.get(ptype, "raw")
                        event = Event(
                            # run_id resolution happens upstream; we put the
                            # message_id in payload for the pipeline to map.
                            run_id="",
                            seq=0,
                            ts=_ms_to_dt(row["time_created"]),
                            kind=kind,
                            text=_summarize_part(part_data),
                            payload={
                                "_opencode_message_id": row["message_id"],
                                "_opencode_session_id": row["session_id"],
                                "_opencode_part_id": row["id"],
                                **part_data,
                            },
                        )
                        try:
                            callback(event)
                        except Exception:  # noqa: BLE001
                            pass
                except sqlite3.OperationalError:
                    # DB might be locked or missing momentarily; retry.
                    pass
                self._stop.wait(self.poll_interval_ms / 1000.0)
            tailer.close()

        self._stop.clear()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

        def stop() -> None:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2.0)

        return Subscription(stop=stop)
