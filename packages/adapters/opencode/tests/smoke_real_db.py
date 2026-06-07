"""Smoke test against the real user database.

Runs only when ~/.local/share/opencode/opencode.db exists. Skipped in CI.
Useful sanity check: does the adapter actually work on the live DB?
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
ADAPTER_DIR = REPO_ROOT / "packages" / "adapters" / "opencode"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ADAPTER_DIR))

from observe import OpenCodeObserver

REAL_DB = Path("~/.local/share/opencode/opencode.db").expanduser()


def main() -> int:
    if not REAL_DB.exists():
        print(f"SKIP: {REAL_DB} not found")
        return 0

    obs = OpenCodeObserver(REAL_DB, recent_hours=1)
    descriptors = obs.list_existing_sessions()
    print(f"sessions in last hour: {len(descriptors)}")
    if not descriptors:
        # Widen the window
        obs = OpenCodeObserver(REAL_DB, recent_hours=72)
        descriptors = obs.list_existing_sessions()
        print(f"sessions in last 72h: {len(descriptors)}")

    if not descriptors:
        print("no sessions found in last 72h — adapter still functional")
        return 0

    most_recent = descriptors[0]
    print(f"\nmost recent: {most_recent.external_id}")
    print(f"  workdir: {most_recent.workdir}")
    print(f"  title:   {most_recent.meta.get('title')}")
    print(f"  model:   {most_recent.meta.get('model')}")
    print(f"  cost:    {most_recent.meta.get('cost')}")

    session, runs, events = obs.parse_session(most_recent)
    print(f"\nparsed session:")
    print(f"  runs:    {len(runs)}")
    print(f"  events:  {len(events)}")

    if events:
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        print(f"  event kind histogram:")
        for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"    {k:25s} {v}")

        first = events[0]
        print(f"\n  first event:")
        print(f"    kind: {first.kind}")
        print(f"    text: {(first.text or '')[:120]}")

    print("\nOK — adapter works on real database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
