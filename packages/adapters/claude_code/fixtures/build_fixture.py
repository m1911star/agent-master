"""Build minimal claude-code-shape JSONL fixture for adapter tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT_DIR = Path(__file__).parent / "fake_projects" / "-Users-test-repo"
SESSION_ID = "abcd1234-5678-90ab-cdef-1234567890ab"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"{SESSION_ID}.jsonl"

    records = [
        # meta lines (should be skipped)
        {"type": "last-prompt", "leafUuid": "aaa", "sessionId": SESSION_ID},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": SESSION_ID},
        # hook event
        {
            "parentUuid": None,
            "isSidechain": False,
            "attachment": {
                "type": "hook_success",
                "hookName": "SessionStart:startup",
                "toolUseID": "hook1",
                "hookEvent": "SessionStart",
                "content": "",
            },
            "timestamp": "2026-06-07T10:00:00Z",
        },
        # user message (string content)
        {
            "role": "user",
            "message": {"role": "user", "content": "Refactor src/foo.ts"},
            "timestamp": "2026-06-07T10:00:01Z",
        },
        # assistant message with thinking + tool_use + text blocks
        {
            "role": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me check the file first."},
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "Read",
                        "input": {"file_path": "src/foo.ts"},
                    },
                ],
            },
            "timestamp": "2026-06-07T10:00:02Z",
        },
        # tool result wrapped in user turn
        {
            "role": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": "file contents...",
                    }
                ],
            },
            "timestamp": "2026-06-07T10:00:03Z",
        },
        # final assistant text
        {
            "role": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Here's the refactor plan..."}],
            },
            "timestamp": "2026-06-07T10:00:04Z",
        },
    ]

    with out_file.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    print(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
