"""Abstract base classes for adapter capabilities.

Per doc/02-adapter.md §Observer 协议 / §Controller 协议 / §Approver 协议.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Approval, Event, Run, Session


# ────────────────────────────────────────────────────────────────────────────
# Observer
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class SessionDescriptor:
    """Lightweight handle returned by list_existing_sessions().

    Observer should not load full event history when scanning — descriptors
    let core decide which sessions to hydrate.
    """

    external_id: str
    workdir: str = ""
    last_active_ts: float | None = None
    raw_path: Path | None = None
    meta: dict[str, Any] | None = None


@dataclass
class Subscription:
    """Returned by Observer.subscribe(); call .stop() to unsubscribe."""

    stop: Callable[[], None]


class Observer(ABC):
    """Maps an agent's private data format into our standard objects.

    Observer is read-only. It must NOT spawn processes, modify the agent's
    files, or call external APIs. It lives next to the agent's storage and
    translates.
    """

    name: str = ""  # subclass sets this; matches adapter.toml [adapter].name

    @abstractmethod
    def list_existing_sessions(self) -> list[SessionDescriptor]:
        """Scan the agent's storage and report all sessions found.

        Called once at startup. Should be cheap — full event history is
        loaded lazily via parse_session().
        """

    @abstractmethod
    def subscribe(self, callback: Callable[[Event], None]) -> Subscription:
        """Watch for new events; invoke callback for each one as they appear."""

    @abstractmethod
    def parse_session(
        self, descriptor: SessionDescriptor
    ) -> tuple[Session, list[Run], list[Event]]:
        """Load full history for one session. Used for hydration / replay."""

    def map_event(self, raw: Any) -> Event | None:
        """Optional: translate one raw record into an Event.

        Default: subclass-specific. Override or compose.
        """
        return None


# ────────────────────────────────────────────────────────────────────────────
# Controller (V0.2+)
# ────────────────────────────────────────────────────────────────────────────


class Controller(ABC):
    """Dispatch tasks to a running agent.

    Three modes per doc/02-adapter.md §Controller 协议:
      - 'spawn'    : start a new agent process per task (claude code, codex)
      - 'inject'   : send a message to an already-running agent (hermes)
      - 'schedule' : write a config / cron entry (heartbeat-style agents)
    """

    name: str = ""
    mode: str = "spawn"  # one of: spawn | inject | schedule

    @abstractmethod
    def dispatch_task(self, task: Any, agent: Any) -> Run:
        """Hand a task to the agent; return the Run that's been created."""

    @abstractmethod
    def pause(self, run_id: str) -> None: ...

    @abstractmethod
    def resume(self, run_id: str) -> None: ...

    @abstractmethod
    def cancel(self, run_id: str) -> None: ...


# ────────────────────────────────────────────────────────────────────────────
# Approver (V0.4+)
# ────────────────────────────────────────────────────────────────────────────


class Approver(ABC):
    """Intercept the agent's dangerous tool calls before they execute.

    Three modes:
      - 'hook'   : agent natively supports a pre-tool hook (Claude Code)
      - 'proxy'  : we MITM the agent's LLM API (works for any agent)
      - 'manual' : user requests approval ad-hoc from the UI
    """

    name: str = ""
    mode: str = "hook"  # one of: hook | proxy | manual

    @abstractmethod
    def setup(self) -> None:
        """Install hook scripts, start proxy, etc. Called once on adapter load."""

    @abstractmethod
    def teardown(self) -> None:
        """Reverse setup() at shutdown."""

    @abstractmethod
    def handle_request(self, raw_request: dict) -> Approval:
        """Translate an incoming approval ask into our Approval object."""
