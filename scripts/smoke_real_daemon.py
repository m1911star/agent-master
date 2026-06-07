"""End-to-end smoke: spin up the API in this process and hit it.

Skips the CLI plumbing — exercises lifespan + endpoints + SSE directly.
Uses a tmp DB so no user state is touched.
"""

from __future__ import annotations

import asyncio
import json
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agent-master-smoke-"))
    db_path = tmp / "state.db"
    print(f"tmp db: {db_path}")

    app = create_app(db_path=db_path)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=18765, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)

    def _run():
        asyncio.run(server.serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Wait for startup
    for _ in range(50):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:18765/api/v1/health", timeout=0.5
            ).read()
            break
        except Exception:
            time.sleep(0.1)
    else:
        print("FAIL: server never came up")
        return 1
    print("server up on :18765")

    try:
        # /health
        body = json.loads(
            urllib.request.urlopen("http://127.0.0.1:18765/api/v1/health").read()
        )
        assert body["status"] == "ok"
        print(f"health: ok (uptime {body['uptime_seconds']:.2f}s)")

        # /agents (empty)
        assert (
            urllib.request.urlopen("http://127.0.0.1:18765/api/v1/agents").read() == b"[]"
        )
        print("agents: []")

        # /sessions (empty)
        assert (
            urllib.request.urlopen("http://127.0.0.1:18765/api/v1/sessions").read() == b"[]"
        )
        print("sessions: []")

        # /internal/metrics
        body = json.loads(
            urllib.request.urlopen(
                "http://127.0.0.1:18765/api/v1/internal/metrics"
            ).read()
        )
        print(f"metrics.pipeline: {body['pipeline']}")
        print(f"metrics.broker.subscriber_total: {body['broker']['subscriber_total']}")

        # SSE — open connection, read 1 line (it'll be the keepalive or first
        # message), then close.
        req = urllib.request.Request(
            "http://127.0.0.1:18765/api/v1/stream/global",
            headers={"Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            ct = r.headers["content-type"]
            assert ct.startswith("text/event-stream"), ct
            print(f"SSE: content-type={ct} — connection opens cleanly")
            # Don't try to read — keep-alive is 15s, we don't want to block.

        print("\nOK — daemon + API + SSE all functional end-to-end")
        return 0

    finally:
        server.should_exit = True
        t.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
