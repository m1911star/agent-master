"""Cross-platform watcher abstraction.

Three watch primitives, one factory:

    - FSWatcher   : filesystem changes via watchfiles (fsevents/inotify/poll)
    - JsonlTail   : incremental read of an append-only JSONL file
    - SqliteTailer: poll a SQLite table for new rows (WAL-friendly)

Per doc/03-realtime.md §跨平台抽象 / §SQLite WAL tail / §增量读 JSONL 文件.
"""

from __future__ import annotations

from .fs import FSWatcher, make_fs_watcher
from .jsonl_tail import JsonlTail
from .sqlite_tail import SqliteTailer

__all__ = ["FSWatcher", "JsonlTail", "SqliteTailer", "make_fs_watcher"]
