"""Mock data seeding — for UI development before real adapters wire in.

Plants a realistic fixture into the daemon's SQLite so the UI can render
a board with non-trivial content before M1.4.5 adapter integration lands.

Shape (per the docs, all kinds + topology covered):
    - 3 agents: opencode / claude_code / hermes
    - 6 sessions: parent + 2 children (sidechain-style), 1 idle, 2 closed
    - 12 runs across them (mix of running / success / failed / interrupted)
    - ~250 events covering every standard Event.kind
    - 4 artifacts (file/pr/commit/document)
    - 2 pending approvals (V0.4 UI preview)
    - 1 active budget + 1 sample rule
"""

from __future__ import annotations

from .mock import seed_mock_data, wipe_mock_data

__all__ = ["seed_mock_data", "wipe_mock_data"]
