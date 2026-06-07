"""Smoke test against the real ~/.hermes/state.db.

Read-only — never mutates the user's hermes data.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from packages.adapters.hermes.observe import HermesObserver

REAL_DB = Path("~/.hermes/state.db").expanduser()


def main() -> int:
    if not REAL_DB.exists():
        print(f"SKIP: {REAL_DB} not found")
        return 0

    obs = HermesObserver(REAL_DB, recent_hours=24)
    descriptors = obs.list_existing_sessions()
    print(f"sessions in last 24h: {len(descriptors)}")

    if not descriptors:
        obs = HermesObserver(REAL_DB, recent_hours=720)
        descriptors = obs.list_existing_sessions()
        print(f"sessions in last 30d: {len(descriptors)}")

    if not descriptors:
        print("no sessions found — adapter still functional")
        return 0

    most_recent = descriptors[0]
    print(f"\nmost recent: {most_recent.external_id}")
    print(f"  source:  {most_recent.meta.get('source')}")
    print(f"  model:   {most_recent.meta.get('model')}")
    print(f"  msgs:    {most_recent.meta.get('message_count')}")
    print(f"  parent:  {most_recent.meta.get('parent_id')}")

    session, runs, events = obs.parse_session(most_recent)
    print(f"\nparsed: {len(runs)} run(s), {len(events)} events")

    if events:
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        print(f"  kind histogram:")
        for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"    {k:25s} {v}")

    print("\nOK — adapter works on real database")
    return 0


if __name__ == "__main__":
    sys.exit(main())
