"""Claude Code Observer adapter.

Maps Claude Code's per-session JSONL files to our standard objects.

Claude Code structure (~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl):
    Each .jsonl is one session. Each line is a JSON record with one of:

        {type: "last-prompt", ...}             — meta
        {type: "permission-mode", ...}         — meta
        {parentUuid, isSidechain, attachment}  — hook event
        {role: "user", message: {...}}         — user turn
        {role: "assistant", message: {content: [{type: "thinking"|"tool_use"|"text", ...}]}}
        {type: "result", tool_use_id, content} — tool result

The session_id is the filename stem. parent_session_id surfaces from the
sidechain root: if the very first content line carries isSidechain=true and
parentUuid points to a uuid in another file, that file is the parent.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_master.adapters.base import (
    Observer,
    SessionDescriptor,
    Subscription,
)
from agent_master.models import Event, Run, Session
from agent_master.watch.fs import FSWatcher
from agent_master.watch.jsonl_tail import JsonlTail


def _parse_iso(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _block_kind(block_type: str) -> str:
    return {
        "thinking": "reasoning",
        "tool_use": "tool_call",
        "text": "assistant_message",
    }.get(block_type, "raw")


class ClaudeCodeObserver(Observer):
    name = "claude_code"

    def __init__(
        self,
        projects_dir: Path,
        *,
        recent_hours: int = 24,
        poll_fallback_ms: int = 1000,
    ) -> None:
        self.projects_dir = Path(projects_dir).expanduser()
        self.recent_hours = recent_hours
        self.poll_fallback_ms = poll_fallback_ms
        self._tails: dict[Path, JsonlTail] = {}
        self._stop = threading.Event()
        self._watcher: FSWatcher | None = None

    def _list_jsonl(self) -> list[Path]:
        if not self.projects_dir.exists():
            return []
        return list(self.projects_dir.glob("*/*.jsonl"))

    def list_existing_sessions(self) -> list[SessionDescriptor]:
        import time

        cutoff = time.time() - self.recent_hours * 3600
        out: list[SessionDescriptor] = []

        for path in self._list_jsonl():
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                continue

            # Decode workdir from the encoded directory name.
            # Pattern: -Users-horus-foo-bar  →  /Users/horus/foo/bar
            encoded = path.parent.name
            workdir = encoded.replace("-", "/", 1) if encoded.startswith("-") else encoded
            workdir = "/" + workdir.lstrip("/").replace("-", "/")

            out.append(
                SessionDescriptor(
                    external_id=path.stem,
                    workdir=workdir,
                    last_active_ts=stat.st_mtime,
                    raw_path=path,
                    meta={
                        "encoded_dir": encoded,
                        "size_bytes": stat.st_size,
                    },
                )
            )

        out.sort(key=lambda d: d.last_active_ts or 0, reverse=True)
        return out

    def parse_session(
        self, descriptor: SessionDescriptor
    ) -> tuple[Session, list[Run], list[Event]]:
        path = descriptor.raw_path or self._find_path(descriptor.external_id)
        if path is None or not path.exists():
            raise FileNotFoundError(descriptor.external_id)

        # Read whole file (bounded — claude code session files are tens of MB max)
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        first_ts = None
        last_ts = None
        is_sidechain = False
        parent_external_id: str | None = None

        for r in records:
            ts_str = r.get("timestamp") or r.get("createdAt")
            if ts_str:
                ts = _parse_iso(ts_str)
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            if r.get("isSidechain"):
                is_sidechain = True

        session = Session(
            agent_id="",  # core fills
            external_id=descriptor.external_id,
            workdir=descriptor.workdir,
            started_at=first_ts or datetime.now(timezone.utc),
            last_active_at=last_ts or datetime.now(timezone.utc),
            ended_at=None,  # claude code doesn't mark "ended"
            status="active",
            summary=None,
            meta={
                "is_sidechain": is_sidechain,
                "parent_external_id": parent_external_id,
                "raw_path": str(path),
            },
        )

        run = Run(
            session_id=session.id,
            trigger="manual",
            started_at=session.started_at,
            ended_at=None,
            status="running",
        )

        events: list[Event] = []
        seq = 0
        for r in records:
            ts_str = r.get("timestamp") or r.get("createdAt")
            ts = _parse_iso(ts_str)
            rtype = r.get("type")
            role = r.get("role") or (r.get("message", {}) or {}).get("role")

            if rtype in ("last-prompt", "permission-mode"):
                # Meta — skip; not user-relevant for V0.1
                continue

            # Hook attachment
            if r.get("attachment"):
                att = r["attachment"]
                events.append(
                    Event(
                        run_id=run.id,
                        seq=seq,
                        ts=ts,
                        kind="status_change",
                        text=f"hook: {att.get('hookName', '')}",
                        payload={"_claude_record": r},
                    )
                )
                seq += 1
                continue

            # User message
            if role == "user":
                msg = r.get("message", {})
                content = msg.get("content")
                if isinstance(content, list):
                    # Could be a tool_result wrapped in a user turn
                    for block in content:
                        btype = block.get("type") if isinstance(block, dict) else None
                        if btype == "tool_result":
                            events.append(
                                Event(
                                    run_id=run.id,
                                    seq=seq,
                                    ts=ts,
                                    kind="tool_result",
                                    text=str(block.get("content", ""))[:300],
                                    payload={"_claude_block": block},
                                )
                            )
                            seq += 1
                        else:
                            text_val = (
                                block.get("text") if isinstance(block, dict) else None
                            ) or str(block)[:300]
                            events.append(
                                Event(
                                    run_id=run.id,
                                    seq=seq,
                                    ts=ts,
                                    kind="user_message",
                                    text=text_val[:300],
                                    payload={"_claude_block": block},
                                )
                            )
                            seq += 1
                else:
                    events.append(
                        Event(
                            run_id=run.id,
                            seq=seq,
                            ts=ts,
                            kind="user_message",
                            text=(content or "")[:300] if isinstance(content, str) else None,
                            payload={"_claude_record": r},
                        )
                    )
                    seq += 1
                continue

            # Assistant message — content is a list of blocks
            if role == "assistant":
                msg = r.get("message", {})
                blocks = msg.get("content", [])
                if not isinstance(blocks, list):
                    blocks = [blocks]

                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    kind = _block_kind(btype)
                    text_val = (
                        block.get("thinking")
                        or block.get("text")
                        or block.get("name")
                        or ""
                    )
                    events.append(
                        Event(
                            run_id=run.id,
                            seq=seq,
                            ts=ts,
                            kind=kind,
                            text=str(text_val)[:300] if text_val else None,
                            payload={"_claude_block": block},
                        )
                    )
                    seq += 1
                continue

            # Standalone tool result
            if rtype == "result":
                events.append(
                    Event(
                        run_id=run.id,
                        seq=seq,
                        ts=ts,
                        kind="tool_result",
                        text=str(r.get("content", ""))[:300],
                        payload={"_claude_record": r},
                    )
                )
                seq += 1
                continue

            # Anything else → raw
            events.append(
                Event(
                    run_id=run.id,
                    seq=seq,
                    ts=ts,
                    kind="raw",
                    payload={"_claude_record": r},
                )
            )
            seq += 1

        return session, [run], events

    def _find_path(self, external_id: str) -> Path | None:
        for path in self._list_jsonl():
            if path.stem == external_id:
                return path
        return None

    def subscribe(self, callback: Callable[[Event], None]) -> Subscription:
        """Watch projects_dir; tail any jsonl that grows.

        On a notification we find which file changed (fsevents reports at
        directory granularity on macOS — we re-scan jsonl mtimes to pick
        the file that grew most recently).
        """

        def on_change(_path: Path, _change: Any) -> None:
            for jsonl in self._list_jsonl():
                tail = self._tails.get(jsonl)
                if tail is None:
                    tail = JsonlTail(jsonl)
                    tail.read_new()  # seed: skip pre-existing content
                    self._tails[jsonl] = tail
                    continue
                for record in tail.read_new():
                    # Use parse_session's mapping logic by emitting a tiny
                    # synthetic per-record event. For V0.1 we pass through
                    # raw + tag with file id.
                    role = record.get("role") or (record.get("message", {}) or {}).get("role")
                    rtype = record.get("type")
                    if rtype in ("last-prompt", "permission-mode"):
                        continue
                    kind = "raw"
                    if role == "user":
                        kind = "user_message"
                    elif role == "assistant":
                        kind = "assistant_message"
                    elif rtype == "result":
                        kind = "tool_result"
                    callback(
                        Event(
                            run_id="",
                            seq=0,
                            ts=_parse_iso(record.get("timestamp")),
                            kind=kind,
                            payload={
                                "_claude_session_id": jsonl.stem,
                                "_claude_record": record,
                            },
                        )
                    )

        watcher = FSWatcher([self.projects_dir], on_change, debounce_ms=300)
        # Seed all existing tails so we only emit new events
        for jsonl in self._list_jsonl():
            t = JsonlTail(jsonl)
            t.read_new()
            self._tails[jsonl] = t

        watcher.start()
        self._watcher = watcher

        def stop() -> None:
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None

        return Subscription(stop=stop)
