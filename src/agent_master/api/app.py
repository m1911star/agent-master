"""FastAPI application factory.

V0.1 surface:
    GET  /api/v1/health
    GET  /api/v1/sessions
    GET  /api/v1/sessions/{id}
    GET  /api/v1/sessions/{id}/events
    GET  /api/v1/runs/{id}
    GET  /api/v1/agents
    GET  /api/v1/stream/global         (SSE)
    GET  /api/v1/stream/run/{run_id}   (SSE)
    GET  /api/v1/internal/metrics

The app holds (via app.state) a single EventBroker + EventPipeline.
Adapters running in the same process (M1.5+) push events through
pipeline.notify_threadsafe. Tests can drive the broker directly.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from .. import __version__
from ..db.connection import connect
from ..db.migrations._runner import migrate
from ..ingest import EventBroker, EventPipeline
from ..logging_setup import get_logger
from ..repo import (
    AgentRepo,
    EventRepo,
    RunRepo,
    SessionRepo,
)


def create_app(db_path: Path | None = None) -> FastAPI:
    """Build the FastAPI app.

    db_path: SQLite file to back queries + the pipeline. When None,
             reads from ~/.agent-master/state.db (resolved via config).
    """
    started_at = time.monotonic()
    log = get_logger("api")

    # Resolve db_path lazily so tests can pass an explicit one.
    if db_path is None:
        from ..config import load_config

        cfg = load_config()
        db_path = Path(cfg.storage.db_path).expanduser()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Ensure schema is up-to-date before exposing any query endpoint.
        with connect(db_path) as conn:
            migrate(conn)

        loop = asyncio.get_running_loop()
        broker = EventBroker()
        pipeline = EventPipeline(db_path, broker, loop)
        await pipeline.start()

        app.state.db_path = db_path
        app.state.broker = broker
        app.state.pipeline = pipeline

        log.info("api_started", version=__version__, db_path=str(db_path))
        try:
            yield
        finally:
            await pipeline.stop()
            await broker.close()
            log.info(
                "api_stopped",
                uptime_seconds=round(time.monotonic() - started_at, 3),
            )

    app = FastAPI(
        title="agent-master",
        version=__version__,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )

    # ── small dependency: borrow a fresh connection per request ──────────
    def _conn():
        return connect(db_path)

    # ── meta ──────────────────────────────────────────────────────────────

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": round(time.monotonic() - started_at, 3),
        }

    @app.get("/api/v1/internal/metrics")
    def metrics() -> dict[str, Any]:
        pipeline: EventPipeline = app.state.pipeline
        broker: EventBroker = app.state.broker
        return {
            "uptime_seconds": round(time.monotonic() - started_at, 3),
            "pipeline": dict(pipeline.stats),
            "broker": {
                "channels": broker.channels(),
                "subscriber_total": sum(
                    broker.subscriber_count(c) for c in broker.channels()
                ),
            },
        }

    # ── query endpoints ──────────────────────────────────────────────────

    @app.get("/api/v1/agents")
    def list_agents(limit: int = 100):
        with _conn() as conn:
            return [a.to_dict() for a in AgentRepo(conn).list(limit=limit)]

    @app.get("/api/v1/sessions")
    def list_sessions(limit: int = 100, active_only: bool = False):
        with _conn() as conn:
            repo = SessionRepo(conn)
            if active_only:
                return [s.to_dict() for s in repo.list_active(limit=limit)]
            return [s.to_dict() for s in repo.list(limit=limit)]

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str):
        with _conn() as conn:
            s = SessionRepo(conn).get(session_id)
            if s is None:
                raise HTTPException(404, f"session {session_id} not found")
            return s.to_dict()

    @app.get("/api/v1/sessions/{session_id}/events")
    def list_session_events(
        session_id: str, after_seq: int | None = None, limit: int = 1000
    ):
        """Aggregates events from all runs in the session, ordered by ts."""
        with _conn() as conn:
            if SessionRepo(conn).get(session_id) is None:
                raise HTTPException(404, f"session {session_id} not found")
            runs = RunRepo(conn).list_by_session(session_id)
            all_events = []
            for r in runs:
                evs = EventRepo(conn).list_by_run(
                    r.id, after_seq=after_seq, limit=limit
                )
                all_events.extend(evs)
            # Order by ts; cap at limit.
            all_events.sort(key=lambda e: e.ts)
            return [e.to_dict() for e in all_events[:limit]]

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        with _conn() as conn:
            r = RunRepo(conn).get(run_id)
            if r is None:
                raise HTTPException(404, f"run {run_id} not found")
            return r.to_dict()

    @app.get("/api/v1/runs/{run_id}/events")
    def list_run_events(
        run_id: str,
        kind: str | None = None,
        after_seq: int | None = None,
        limit: int = 1000,
    ):
        with _conn() as conn:
            if RunRepo(conn).get(run_id) is None:
                raise HTTPException(404, f"run {run_id} not found")
            evs = EventRepo(conn).list_by_run(
                run_id, kind=kind, after_seq=after_seq, limit=limit
            )
            return [e.to_dict() for e in evs]

    # ── SSE streaming ────────────────────────────────────────────────────

    async def _sse_iter(
        broker: EventBroker, channel: str, request: Request
    ) -> AsyncIterator[dict]:
        sub = await broker.subscribe(channel)
        try:
            async for msg in broker.stream(sub):
                if await request.is_disconnected():
                    break
                yield {"event": msg.get("type", "message"),
                       "data": json.dumps(msg)}
        finally:
            await broker.unsubscribe(sub)

    @app.get("/api/v1/stream/global")
    async def stream_global(request: Request):
        broker: EventBroker = app.state.broker
        return EventSourceResponse(
            _sse_iter(broker, "global", request),
            ping=15,
        )

    @app.get("/api/v1/stream/run/{run_id}")
    async def stream_run(run_id: str, request: Request):
        broker: EventBroker = app.state.broker
        return EventSourceResponse(
            _sse_iter(broker, f"run:{run_id}", request),
            ping=15,
        )

    return app


# Convenience for `uvicorn agent_master.api.app:app`
app = create_app()
