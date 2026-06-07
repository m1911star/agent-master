"""Hermes Observer adapter.

Maps Hermes's session/message SQLite schema to our standard objects.

Hermes structure (~/.hermes/state.db):

    sessions(id, source, parent_session_id, started_at, ended_at,
             model, input_tokens, output_tokens, ...)
      ↓ 1:N
    messages(id, session_id, role, content, tool_calls, tool_name,
             reasoning, timestamp, ...)

Mapping:
    session     → Session (parent_session_id directly carries topology)
    messages    → Events (no intermediate Run; the whole session is one Run
                  for V0.1 — we'll split if it turns out to matter)

message.role values seen in the wild:
    user / assistant / tool / system → user_message / assistant_message /
                                       tool_result / status_change

If message.tool_calls is non-null AND role == 'assistant', that single
message becomes both an assistant_message Event AND a tool_call Event
(one row produces two events, each with their own seq).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from agent_master.adapters.base import (
    Observer,
    SessionDescriptor,
    Subscription,
)
from agent_master.models import Event, Run, Session
from agent_master.watch.sqlite_tail import SqliteTailer


def _epoch_to_dt(ts: float | int | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


ROLE_TO_KIND = {
    "user": "user_message",
    "assistant": "assistant_message",
    "tool": "tool_result",
    "system": "status_change",
}


class HermesObserver(Observer):
    name = "hermes"

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

    def _readonly_conn(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def list_existing_sessions(self) -> list[SessionDescriptor]:
        if not self.db_path.exists():
            return []

        import time

        cutoff = time.time() - self.recent_hours * 3600
        conn = self._readonly_conn()
        try:
            rows = conn.execute(
                "SELECT id, source, parent_session_id, started_at, ended_at, "
                "model, message_count, tool_call_count, input_tokens, output_tokens "
                "FROM sessions WHERE started_at >= ? "
                "ORDER BY COALESCE(ended_at, started_at) DESC",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()

        return [
            SessionDescriptor(
                external_id=r["id"],
                workdir="",  # hermes doesn't track cwd at session level
                last_active_ts=r["ended_at"] or r["started_at"],
                meta={
                    "source": r["source"],
                    "parent_id": r["parent_session_id"],
                    "model": r["model"],
                    "message_count": r["message_count"],
                    "tool_call_count": r["tool_call_count"],
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                },
            )
            for r in rows
        ]

    def parse_session(
        self, descriptor: SessionDescriptor
    ) -> tuple[Session, list[Run], list[Event]]:
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

        conn = self._readonly_conn()
        try:
            srow = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (descriptor.external_id,)
            ).fetchone()
            if srow is None:
                raise ValueError(f"session {descriptor.external_id} not found")

            session = Session(
                agent_id="",  # core fills in
                external_id=srow["id"],
                workdir="",
                started_at=_epoch_to_dt(srow["started_at"]),
                last_active_at=_epoch_to_dt(
                    srow["ended_at"] or srow["started_at"]
                ),
                ended_at=_epoch_to_dt(srow["ended_at"]) if srow["ended_at"] else None,
                status="closed" if srow["ended_at"] else "active",
                summary=None,
                meta={
                    "hermes_parent_session_id": srow["parent_session_id"],
                    "source": srow["source"],
                    "model": srow["model"],
                    "input_tokens": srow["input_tokens"],
                    "output_tokens": srow["output_tokens"],
                    "cache_read_tokens": srow["cache_read_tokens"],
                    "cache_write_tokens": srow["cache_write_tokens"],
                    "reasoning_tokens": srow["reasoning_tokens"],
                    "tool_call_count": srow["tool_call_count"],
                    "billing_provider": srow["billing_provider"],
                },
            )

            mrows = conn.execute(
                "SELECT id, role, content, tool_calls, tool_name, "
                "reasoning, timestamp, finish_reason, token_count "
                "FROM messages WHERE session_id = ? "
                "ORDER BY timestamp, id",
                (srow["id"],),
            ).fetchall()
        finally:
            conn.close()

        # V0.1: one Run per session. M1.4 may split at finish_reason boundaries.
        run = Run(
            session_id=session.id,
            trigger="manual",
            started_at=session.started_at,
            ended_at=session.ended_at,
            status="success" if session.ended_at else "running",
            tokens_in=int(srow["input_tokens"] or 0),
            tokens_out=int(srow["output_tokens"] or 0),
        )
        runs = [run]

        events: list[Event] = []
        seq = 0
        for m in mrows:
            ts = _epoch_to_dt(m["timestamp"])
            kind = ROLE_TO_KIND.get(m["role"], "raw")

            # Reasoning is its own event before the user/assistant content.
            if m["reasoning"]:
                events.append(
                    Event(
                        run_id=run.id,
                        seq=seq,
                        ts=ts,
                        kind="reasoning",
                        text=str(m["reasoning"])[:300],
                        payload={
                            "_hermes_message_id": m["id"],
                            "reasoning": m["reasoning"],
                        },
                    )
                )
                seq += 1

            # Main content event
            events.append(
                Event(
                    run_id=run.id,
                    seq=seq,
                    ts=ts,
                    kind=kind,
                    text=(m["content"] or "")[:300] if m["content"] else None,
                    payload={
                        "_hermes_message_id": m["id"],
                        "role": m["role"],
                        "content": m["content"],
                        "finish_reason": m["finish_reason"],
                        "token_count": m["token_count"],
                    },
                )
            )
            seq += 1

            # If the assistant emitted tool_calls, expand each into a tool_call event
            if m["tool_calls"] and m["role"] == "assistant":
                try:
                    calls = json.loads(m["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    calls = []
                if isinstance(calls, list):
                    for call in calls:
                        tname = call.get("function", {}).get("name") if isinstance(call, dict) else None
                        events.append(
                            Event(
                                run_id=run.id,
                                seq=seq,
                                ts=ts,
                                kind="tool_call",
                                text=tname or "tool",
                                payload={
                                    "_hermes_message_id": m["id"],
                                    "tool_call": call,
                                },
                            )
                        )
                        seq += 1

            # tool role messages also carry tool_name
            if m["role"] == "tool" and m["tool_name"]:
                # already emitted as kind=tool_result above; just enrich payload
                events[-1].text = m["tool_name"]
                events[-1].payload["tool_name"] = m["tool_name"]

        return session, runs, events

    def subscribe(self, callback: Callable[[Event], None]) -> Subscription:
        tailer = SqliteTailer(self.db_path, "messages", cursor_col="id", order_col="id")
        tailer.seed()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    rows = tailer.poll()
                    for row in rows:
                        kind = ROLE_TO_KIND.get(row["role"], "raw")
                        callback(
                            Event(
                                run_id="",
                                seq=0,
                                ts=_epoch_to_dt(row["timestamp"]),
                                kind=kind,
                                text=(row["content"] or "")[:300]
                                if row["content"]
                                else None,
                                payload={
                                    "_hermes_message_id": row["id"],
                                    "_hermes_session_id": row["session_id"],
                                    "role": row["role"],
                                    "tool_calls": row["tool_calls"],
                                    "tool_name": row["tool_name"],
                                },
                            )
                        )
                except sqlite3.OperationalError:
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
