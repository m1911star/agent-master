"""FastAPI application factory.

V0.1 surface is intentionally small — just /health for now. Real endpoints
(sessions, events, stream, ...) come online in M1.2/M1.3.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .. import __version__
from ..logging_setup import get_logger


def create_app() -> FastAPI:
    started_at = time.monotonic()
    log = get_logger("api")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log.info("api_started", version=__version__)
        try:
            yield
        finally:
            log.info("api_stopped", uptime_seconds=round(time.monotonic() - started_at, 3))

    app = FastAPI(
        title="agent-master",
        version=__version__,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        payload = {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": round(time.monotonic() - started_at, 3),
        }
        log.debug("health_check", **payload)
        return payload

    return app


# Convenience for `uvicorn agent_master.api.app:app`
app = create_app()
