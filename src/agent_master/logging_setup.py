"""structlog configuration — JSON line output, one line per event.

Format matches doc/06-architecture.md §日志:
    {"ts": "...Z", "level": "info", "component": "...", "event": "...", ...}
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def _level_to_int(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)


def configure_logging(level: str = "info") -> None:
    """Wire up stdlib logging + structlog to emit JSON lines on stderr.

    Idempotent — safe to call more than once (uvicorn reloads, tests, etc.).
    """
    log_level = _level_to_int(level)

    # Stdlib root: send everything to stderr, no extra formatting (structlog handles it).
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(log_level)

    # Quiet uvicorn's access log slightly — it's noisy at info.
    logging.getLogger("uvicorn.access").setLevel(max(log_level, logging.WARNING))

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts")

    structlog.configure(
        cache_logger_on_first_use=True,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def get_logger(component: str, **bound: Any) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name (e.g. 'api', 'adapter.opencode')."""
    return structlog.get_logger().bind(component=component, **bound)
