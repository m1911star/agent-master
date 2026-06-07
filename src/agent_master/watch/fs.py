"""Filesystem watcher backed by watchfiles (fsevents on mac, inotify on linux).

Wrapper kept thin — watchfiles already does cross-platform detection. We add:
    - a stop() method (so callers can manage lifecycle without async ceremony)
    - a synchronous callback interface (the rest of the codebase isn't async)
    - graceful behavior when watched paths don't exist yet

Per doc/03-realtime.md §跨平台抽象.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from watchfiles import Change, watch


class FSWatcher:
    """Watches one or more paths; calls callback(path, change) on each event.

    Usage:
        w = FSWatcher([Path("~/.claude/projects").expanduser()], on_change)
        w.start()
        ...
        w.stop()
    """

    def __init__(
        self,
        paths: Iterable[Path],
        callback: Callable[[Path, Change], None],
        *,
        debounce_ms: int = 300,
        recursive: bool = True,
    ) -> None:
        self.paths = [Path(p) for p in paths]
        self.callback = callback
        self.debounce_ms = debounce_ms
        self.recursive = recursive
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        # watchfiles raises on nonexistent paths; filter them up-front.
        existing = [str(p) for p in self.paths if p.exists()]
        if not existing:
            return
        for changes in watch(
            *existing,
            stop_event=self._stop_event,
            debounce=self.debounce_ms,
            recursive=self.recursive,
            raise_interrupt=False,
        ):
            for change, path in changes:
                try:
                    self.callback(Path(path), change)
                except Exception:  # noqa: BLE001 — adapter error must not kill watcher
                    # In production this would log via structlog; for now silence
                    # to keep the watcher alive across bad callbacks.
                    pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


def make_fs_watcher(
    paths: Iterable[Path],
    callback: Callable[[Path, Change], None],
    **kwargs: object,
) -> FSWatcher:
    """Factory for forward-compat. Today watchfiles is good enough on every
    supported OS, so there's no branch — keeping the factory signature for the
    day someone ships a fancy native backend."""
    return FSWatcher(paths, callback, **kwargs)  # type: ignore[arg-type]
