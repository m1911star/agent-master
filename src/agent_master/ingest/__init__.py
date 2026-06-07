"""Ingest pipeline + Pub/Sub broker.

The pipeline is the single funnel through which all adapter events become
both (a) persisted Event rows in SQLite and (b) live broadcasts to UI
subscribers.

Per doc/03-realtime.md §Backend pipeline + §防抖与降噪.

Design:
    Adapter -> EventPipeline.notify(event)
                 |
                 +--> DB.write_event(event)               (always; cheap, sync)
                 |
                 +--> EventBroker.publish_debounced(...)  (per-session 300ms)
                                       |
                                       v
                              SSE subscribers (best-effort fanout)

The broker holds in-memory queues per channel (channel = "session:<id>"
for now; "global" for the home view). Subscribers are async generators.
"""

from __future__ import annotations

from .broker import EventBroker, Subscription
from .pipeline import EventPipeline

__all__ = ["EventBroker", "EventPipeline", "Subscription"]
