"""End-to-end smoke: seed mock data, spin up daemon, hit every endpoint."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import uvicorn

from agent_master.api.app import create_app
from agent_master.seed import seed_mock_data


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agent-master-mock-"))
    db = tmp / "state.db"
    print(f"tmp db: {db}")

    summary = seed_mock_data(db)
    print(f"seeded: {summary}")

    port = _free_port()
    app = create_app(db_path=db)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    def _run():
        asyncio.run(server.serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    print(f"server up :{port}\n")

    try:
        base = f"http://127.0.0.1:{port}/api/v1"

        s, agents = _get(f"{base}/agents")
        print(f"GET /agents → {s} ({len(agents)} agents)")
        for a in agents:
            print(f"  • {a['name']:30s} [{a['adapter_type']:12s}] {a['status']}")

        s, sessions = _get(f"{base}/sessions")
        print(f"\nGET /sessions → {s} ({len(sessions)} sessions)")
        for sess in sessions:
            print(f"  • {sess['external_id']:30s} {sess['status']:8s} "
                  f"{(sess.get('summary') or '')[:50]}")

        s, active = _get(f"{base}/sessions?active_only=true")
        print(f"\nGET /sessions?active_only → {len(active)} active")

        # Pick a session with events
        sid = sessions[0]["id"]
        s, events = _get(f"{base}/sessions/{sid}/events?limit=200")
        print(f"\nGET /sessions/{sid[:8]}.../events → {s} ({len(events)} events)")
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  • {k:20s} {v}")

        s, metrics = _get(f"{base}/internal/metrics")
        print(f"\nGET /internal/metrics → {s}")
        print(f"  uptime: {metrics['uptime_seconds']:.2f}s")
        print(f"  channels: {metrics['broker']['channels']}")

        print("\nOK — daemon + seeded API all functional with mock data")
        return 0
    finally:
        server.should_exit = True
        t.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
