"""End-to-end SSE tests.

ASGITransport + httpx + lifespan_context don't play nice for long-lived
SSE connections (lifespan_context blocks the test, ASGITransport's
lifespan isn't reentrant). Instead spin up real uvicorn on a free port
in a background thread — the same pattern smoke_real_daemon.py uses,
just adapted to pytest.

This is the canonical SSE round-trip test: broker.publish() → real HTTP
GET on /api/v1/stream/global → client parses the SSE frame.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from agent_master.api.app import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerHarness:
    """Spin up a real uvicorn in a background thread and shut it down cleanly."""

    def __init__(self, app, port: int):
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
        self.server = uvicorn.Server(config)
        self.thread: threading.Thread | None = None
        self.app = app

    def start(self, ready_timeout: float = 10.0) -> None:
        def _run() -> None:
            asyncio.run(self.server.serve())

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if self.server.started:
                return
            time.sleep(0.05)
        raise TimeoutError("uvicorn never started")

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)


@pytest.fixture
def live_server(tmp_path: Path):
    """Yield (port, app) for a running daemon on a free port."""
    port = _free_port()
    app = create_app(db_path=tmp_path / "state.db")
    h = _ServerHarness(app, port)
    h.start()
    try:
        yield port, app
    finally:
        h.stop()


@pytest.mark.asyncio
async def test_sse_global_receives_published_message(live_server):
    port, app = live_server

    received: list[dict] = []
    ready = asyncio.Event()
    done = asyncio.Event()

    async def consume() -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/v1/stream/global",
                headers={"Accept": "text/event-stream"},
            ) as resp:
                assert resp.status_code == 200
                ready.set()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            received.append(json.loads(line[len("data:"):].strip()))
                        except json.JSONDecodeError:
                            continue
                        if len(received) >= 1:
                            done.set()
                            return

    task = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), timeout=5.0)
    # Give the broker.subscribe call a tick to register the subscription
    # on the server's event loop.
    await asyncio.sleep(0.2)

    # Publish from the server's broker (need to schedule on its loop)
    broker = app.state.broker
    server_loop = app.state.pipeline.loop
    fut = asyncio.run_coroutine_threadsafe(
        broker.publish("global", {"type": "event", "kind": "test", "x": 42}),
        server_loop,
    )
    fut.result(timeout=2.0)

    await asyncio.wait_for(done.wait(), timeout=5.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert len(received) == 1
    assert received[0]["kind"] == "test"
    assert received[0]["x"] == 42


@pytest.mark.asyncio
async def test_sse_run_channel_isolation(live_server):
    port, app = live_server
    broker = app.state.broker
    server_loop = app.state.pipeline.loop

    async def consume_one(channel_id: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}/api/v1/stream/run/{channel_id}",
                headers={"Accept": "text/event-stream"},
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        return json.loads(line[len("data:"):].strip())
        raise RuntimeError("stream closed without data")

    t1 = asyncio.create_task(consume_one("run_a"))
    t2 = asyncio.create_task(consume_one("run_b"))
    await asyncio.sleep(0.3)  # let both subscribe

    asyncio.run_coroutine_threadsafe(
        broker.publish("run:run_a", {"type": "session_update", "a": 1}),
        server_loop,
    ).result(timeout=2.0)
    asyncio.run_coroutine_threadsafe(
        broker.publish("run:run_b", {"type": "session_update", "b": 2}),
        server_loop,
    ).result(timeout=2.0)

    m1 = await asyncio.wait_for(t1, timeout=5.0)
    m2 = await asyncio.wait_for(t2, timeout=5.0)

    assert m1.get("a") == 1 and "b" not in m1
    assert m2.get("b") == 2 and "a" not in m2
