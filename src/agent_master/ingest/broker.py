"""In-memory async pub/sub broker.

Channels are keyed by name (e.g. "session:abc123", "global"). Subscribers
get a per-subscription asyncio.Queue. Publishers fan out to all current
subscribers; if a subscriber's queue is full, the message is dropped for
that subscriber rather than blocking the publisher.

This is local-only — no Redis, no Kafka. V0.1 we have one daemon process
and dozens of subscribers at most.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Subscription:
    id: str
    channel: str
    queue: asyncio.Queue
    created_at: float = field(default_factory=time.time)


class EventBroker:
    """One broker per daemon. Thread-safe via asyncio primitives (assume
    callers are in the same event loop or use broker.publish_threadsafe).
    """

    def __init__(self, queue_size: int = 1000) -> None:
        self.queue_size = queue_size
        self._channels: dict[str, list[Subscription]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str) -> Subscription:
        sub = Subscription(
            id=uuid.uuid4().hex[:8],
            channel=channel,
            queue=asyncio.Queue(maxsize=self.queue_size),
        )
        async with self._lock:
            self._channels.setdefault(channel, []).append(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            subs = self._channels.get(sub.channel)
            if subs is None:
                return
            self._channels[sub.channel] = [s for s in subs if s.id != sub.id]
            if not self._channels[sub.channel]:
                del self._channels[sub.channel]

    def channels(self) -> list[str]:
        return list(self._channels.keys())

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, []))

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """Fan out. Returns the number of subscribers reached.

        If a subscriber's queue is full, the message is dropped *for that
        subscriber only*. This protects the publisher from slow clients.
        """
        async with self._lock:
            subs = list(self._channels.get(channel, []))
        delivered = 0
        for sub in subs:
            try:
                sub.queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                # Subscriber is too slow. Skip them for this message.
                pass
        return delivered

    def publish_threadsafe(
        self, loop: asyncio.AbstractEventLoop, channel: str, message: dict[str, Any]
    ) -> None:
        """Schedule a publish from a non-event-loop thread.

        Adapters watch in their own threads (FSWatcher, SqliteTailer); they
        use this to push into the broker without needing their own loop.
        """
        asyncio.run_coroutine_threadsafe(self.publish(channel, message), loop)

    async def stream(self, sub: Subscription) -> AsyncIterator[dict[str, Any]]:
        """Yield messages until the subscriber is unsubscribed or cancelled."""
        try:
            while True:
                msg = await sub.queue.get()
                if msg is _SENTINEL_STOP:
                    return
                yield msg
        except asyncio.CancelledError:
            return

    async def close(self) -> None:
        """Send stop sentinel to all current subscribers (so their streams exit)."""
        async with self._lock:
            all_subs = [s for subs in self._channels.values() for s in subs]
            self._channels.clear()
        for sub in all_subs:
            try:
                sub.queue.put_nowait(_SENTINEL_STOP)
            except asyncio.QueueFull:
                pass


_SENTINEL_STOP = object()
