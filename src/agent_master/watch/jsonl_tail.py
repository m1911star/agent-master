"""Incremental JSONL tailing.

Append-only JSONL files (Claude Code, Codex) are read in chunks, parsing
only the lines added since last call. Inode tracking handles file rotation
and truncation.

Per doc/03-realtime.md §增量读 JSONL 文件.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonlTail:
    """Reads new JSONL records since last poll.

    State:
        offset  : byte offset into file
        inode   : file inode (detects rotation/truncate)

    Each call to read_new() returns parsed dicts for newly-appended lines.
    Partial last-line (no trailing newline) is left in the buffer for the
    next call.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.offset: int = 0
        self.inode: int | None = None

    def _stat_inode(self) -> int | None:
        try:
            return os.stat(self.path).st_ino
        except FileNotFoundError:
            return None

    def read_new(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        current_inode = self._stat_inode()

        # Detect rotation/truncate: inode changed or file shrunk
        if self.inode is not None and current_inode != self.inode:
            self.offset = 0
        if self.inode is None:
            self.inode = current_inode

        with self.path.open("rb") as f:
            f.seek(0, 2)  # SEEK_END
            size = f.tell()
            if size < self.offset:
                # truncated — start over
                self.offset = 0
            f.seek(self.offset)
            data = f.read()
            self.offset = f.tell()

        # Hold back trailing partial line.
        if not data.endswith(b"\n") and data:
            last_nl = data.rfind(b"\n")
            if last_nl == -1:
                # no complete line at all yet
                self.offset -= len(data)
                return []
            partial = len(data) - (last_nl + 1)
            self.offset -= partial
            data = data[: last_nl + 1]

        results: list[dict[str, Any]] = []
        for raw in data.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                # Malformed line — skip. In production log this.
                continue
        return results

    def reset(self) -> None:
        """Forget state (rewinds to start on next read_new)."""
        self.offset = 0
        self.inode = None
