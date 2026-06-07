"""Tests for the EventPipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from agent_master.db import connect, migrate
from agent_master.ingest import EventBroker, EventPipeline
from agent_master.models import Agent, Event, Run, Session
from agent_master.repo import AgentRepo, EventRepo, RunRepo, SessionRepo


@pytest_asyncio.fixture
async def setup(tmp_path: Path):
    db_path = tmp_path / "state.db"
    # Create schema + seed agent/session/run
    with connect(db_path) as conn:
        migrate(conn)
        agent = AgentRepo(conn).create(Agent(name="t", adapter_type="opencode"))
        session = SessionRepo(conn).create(Session(agent_id=agent.id))
        run = RunRepo(conn).create(Run(session_id=session.id, trigger="manual"))

    broker = EventBroker()
    loop = asyncio.get_running_loop()
    pipeline = EventPipeline(db_path, broker, loop, debounce_ms=50)
    await pipeline.start()

    try:
        yield {"pipeline": pipeline, "broker": broker, "db_path": db_path,
               "agent": agent, "session": session, "run": run}
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_notify_persists_event(setup):
    s = setup
    e = Event(run_id=s["run"].id, seq=0, kind="user_message", text="hi")
    await s["pipeline"].notify(e)

    # Persisted?
    with connect(s["db_path"]) as conn:
        rows = EventRepo(conn).list_by_run(s["run"].id)
        assert len(rows) == 1
        assert rows[0].text == "hi"

    assert s["pipeline"].stats["events_persisted"] == 1


@pytest.mark.asyncio
async def test_notify_auto_assigns_seq(setup):
    s = setup
    for i in range(3):
        await s["pipeline"].notify(
            Event(run_id=s["run"].id, kind="user_message", text=f"msg{i}")
        )

    with connect(s["db_path"]) as conn:
        rows = EventRepo(conn).list_by_run(s["run"].id)
        seqs = sorted(r.seq for r in rows)
        # First event on empty run starts at max_seq(-1)+1 = 0; then 1, 2.
        assert seqs == [0, 1, 2]


@pytest.mark.asyncio
async def test_notify_broadcasts_to_global(setup):
    s = setup
    sub = await s["broker"].subscribe("global")

    await s["pipeline"].notify(
        Event(run_id=s["run"].id, kind="user_message", text="hello")
    )
    msg = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert msg["type"] == "event"
    assert msg["kind"] == "user_message"
    assert msg["text"] == "hello"


@pytest.mark.asyncio
async def test_debounce_coalesces_burst(setup):
    """Bursts of events on the same run get one summary broadcast, not N."""
    s = setup
    sub = await s["broker"].subscribe(f"run:{s['run'].id}")

    # Fire 5 events fast
    for i in range(5):
        await s["pipeline"].notify(
            Event(run_id=s["run"].id, kind="tool_call", text=f"t{i}")
        )

    # Wait for the debounce window to elapse
    await asyncio.sleep(0.2)  # debounce_ms is 50

    # Should see one summary message, not 5
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())

    assert len(msgs) == 1
    assert msgs[0]["type"] == "session_update"
    assert msgs[0]["event_count"] == 5
    assert msgs[0]["latest_text"] == "t4"


@pytest.mark.asyncio
async def test_two_separate_runs_two_summaries(setup):
    """Different runs flush independently."""
    s = setup

    # Make a second run
    with connect(s["db_path"]) as conn:
        run2 = RunRepo(conn).create(Run(session_id=s["session"].id, trigger="manual"))

    sub1 = await s["broker"].subscribe(f"run:{s['run'].id}")
    sub2 = await s["broker"].subscribe(f"run:{run2.id}")

    await s["pipeline"].notify(Event(run_id=s["run"].id, kind="user_message"))
    await s["pipeline"].notify(Event(run_id=run2.id, kind="user_message"))

    await asyncio.sleep(0.2)

    m1 = sub1.queue.get_nowait()
    m2 = sub2.queue.get_nowait()
    assert m1["channel"] == f"run:{s['run'].id}"
    assert m2["channel"] == f"run:{run2.id}"


@pytest.mark.asyncio
async def test_global_channel_emits_every_event_not_just_summary(setup):
    """Global watchers see every event (not debounced)."""
    s = setup
    sub = await s["broker"].subscribe("global")

    for i in range(3):
        await s["pipeline"].notify(
            Event(run_id=s["run"].id, kind="tool_call", text=f"t{i}")
        )

    msgs = []
    for _ in range(3):
        msgs.append(await asyncio.wait_for(sub.queue.get(), timeout=1.0))

    assert len(msgs) == 3
    assert all(m["type"] == "event" for m in msgs)
    assert [m["text"] for m in msgs] == ["t0", "t1", "t2"]


@pytest.mark.asyncio
async def test_notify_threadsafe_from_background_thread(setup):
    """Adapters run in their own threads — notify_threadsafe must schedule
    the notify coroutine on the loop without deadlocking the publisher."""
    import threading

    s = setup
    sub = await s["broker"].subscribe("global")

    fired = threading.Event()

    def adapter_thread() -> None:
        # Pretend we're an OpenCode/Hermes adapter polling thread.
        for i in range(5):
            s["pipeline"].notify_threadsafe(
                Event(run_id=s["run"].id, kind="user_message", text=f"bg{i}")
            )
        fired.set()

    t = threading.Thread(target=adapter_thread)
    t.start()
    # Wait for the thread to finish enqueueing
    assert fired.wait(timeout=2.0)
    t.join(timeout=2.0)

    # Collect from the loop side
    received = []
    for _ in range(5):
        msg = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        received.append(msg)

    assert len(received) == 5
    assert [m["text"] for m in received] == ["bg0", "bg1", "bg2", "bg3", "bg4"]
    # All persisted too
    assert s["pipeline"].stats["events_persisted"] == 5
