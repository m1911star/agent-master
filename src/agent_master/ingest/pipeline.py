"""Event ingest pipeline.

Adapters drop Event objects in via `notify()`; the pipeline:
    1. Stamps a fresh seq within the run (using max(seq)+1 if not set)
    2. Persists to SQLite via EventRepo
    3. Schedules a debounced broadcast through EventBroker

Debouncing collapses bursts of events for the same session into a single
"hey something changed" notification at ≤ 3Hz per session (default 300ms
window). The full events are still in the DB; the broadcast carries a
summary + sequence pointer so subscribers know to fetch deltas.

Per doc/03-realtime.md §防抖与降噪.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable

from ..db.connection import connect
from ..models import Event
from ..repo import EventRepo
from .broker import EventBroker

# Type alias for the optional post-persist hook (UI / metrics / etc.)
PostPersistHook = Callable[[Event], Awaitable[None]]


class EventPipeline:
    """Funnels adapter events to DB + broker.

    Usage:
        pipeline = EventPipeline(db_path, broker, loop)
        await pipeline.start()
        ...
        await pipeline.notify(event)
        ...
        await pipeline.stop()
    """

    def __init__(
        self,
        db_path,
        broker: EventBroker,
        loop: asyncio.AbstractEventLoop,
        *,
        debounce_ms: int = 300,
    ) -> None:
        self.db_path = db_path
        self.broker = broker
        self.loop = loop
        self.debounce_ms = debounce_ms

        # Per-bucket pending events + flush timers.
        # Accessed only from the event loop thread → no lock needed.
        self._pending: dict[str, list[Event]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

        # DB connection (single, used from the loop thread only)
        self._conn = None
        self._event_repo = None

        # Stats — useful for /api/internal/metrics later
        self.stats = {
            "events_received": 0,
            "events_persisted": 0,
            "broadcasts_sent": 0,
            "broadcasts_dropped": 0,
        }

    async def start(self) -> None:
        self._conn = connect(self.db_path)
        self._event_repo = EventRepo(self._conn)

    async def stop(self) -> None:
        # Cancel pending timers
        for handle in self._timers.values():
            handle.cancel()
        # Flush remaining synchronously
        for bucket in list(self._pending.keys()):
            await self._flush(bucket)
        self._timers.clear()
        self._pending.clear()
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── ingestion ──────────────────────────────────────────────────────

    async def notify(
        self,
        event: Event,
        *,
        channel: str | None = None,
    ) -> None:
        """Persist + schedule debounced broadcast."""
        self.stats["events_received"] += 1

        # Auto-assign seq within run if caller didn't
        if event.seq is None or event.seq == 0:
            assert self._event_repo is not None
            event.seq = self._event_repo.max_seq(event.run_id) + 1

        # Persist
        assert self._event_repo is not None
        self._event_repo.create(event)
        self.stats["events_persisted"] += 1

        # Always broadcast to global channel (home view watches this)
        await self._broadcast_global(event)

        # Debounce per-bucket broadcasts
        bucket = channel or f"run:{event.run_id}"
        self._pending.setdefault(bucket, []).append(event)
        if bucket not in self._timers:
            self._timers[bucket] = self.loop.call_later(
                self.debounce_ms / 1000.0,
                self._schedule_flush,
                bucket,
            )

    def _schedule_flush(self, bucket: str) -> None:
        """call_later callback runs synchronously; we schedule the async flush."""
        self._timers.pop(bucket, None)
        asyncio.ensure_future(self._flush(bucket), loop=self.loop)

    def notify_threadsafe(
        self, event: Event, *, channel: str | None = None
    ) -> None:
        """For adapter threads: schedule the async notify on the loop."""
        asyncio.run_coroutine_threadsafe(
            self.notify(event, channel=channel), self.loop
        )

    async def _broadcast_global(self, event: Event) -> None:
        msg = {
            "type": "event",
            "kind": event.kind,
            "run_id": event.run_id,
            "seq": event.seq,
            "ts": event.ts.isoformat() if event.ts else None,
            "text": event.text,
        }
        delivered = await self.broker.publish("global", msg)
        self.stats["broadcasts_sent"] += delivered

    async def _flush(self, bucket: str) -> None:
        events = self._pending.pop(bucket, [])
        if not events:
            return
        summary = {
            "type": "session_update",
            "channel": bucket,
            "event_count": len(events),
            "latest_seq": events[-1].seq,
            "latest_kind": events[-1].kind,
            "latest_text": events[-1].text,
            "ts": time.time(),
        }
        delivered = await self.broker.publish(bucket, summary)
        self.stats["broadcasts_sent"] += delivered
