"""Tests for the in-memory EventBroker."""

from __future__ import annotations

import asyncio

import pytest

from agent_master.ingest import EventBroker


@pytest.mark.asyncio
async def test_publish_to_no_subscribers_returns_zero():
    broker = EventBroker()
    delivered = await broker.publish("nobody", {"msg": "hi"})
    assert delivered == 0


@pytest.mark.asyncio
async def test_subscribe_and_receive():
    broker = EventBroker()
    sub = await broker.subscribe("ch1")

    delivered = await broker.publish("ch1", {"x": 1})
    assert delivered == 1
    assert broker.subscriber_count("ch1") == 1

    msg = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert msg == {"x": 1}


@pytest.mark.asyncio
async def test_multiple_subscribers_fanout():
    broker = EventBroker()
    s1 = await broker.subscribe("ch1")
    s2 = await broker.subscribe("ch1")
    s3 = await broker.subscribe("ch2")

    delivered = await broker.publish("ch1", {"hello": "world"})
    assert delivered == 2

    assert (await s1.queue.get()) == {"hello": "world"}
    assert (await s2.queue.get()) == {"hello": "world"}
    assert s3.queue.empty()


@pytest.mark.asyncio
async def test_unsubscribe():
    broker = EventBroker()
    sub = await broker.subscribe("ch1")
    assert broker.subscriber_count("ch1") == 1

    await broker.unsubscribe(sub)
    assert broker.subscriber_count("ch1") == 0
    delivered = await broker.publish("ch1", {"x": 1})
    assert delivered == 0


@pytest.mark.asyncio
async def test_slow_subscriber_dropped_not_blocking():
    """If a subscriber's queue is full, the publisher should NOT block;
    the message is dropped for that subscriber only."""
    broker = EventBroker(queue_size=2)
    slow = await broker.subscribe("ch1")
    fast = await broker.subscribe("ch1")

    # Fill slow's queue
    await broker.publish("ch1", {"n": 1})
    await broker.publish("ch1", {"n": 2})

    # Fast subscriber drains; slow does not
    assert fast.queue.qsize() == 2
    assert slow.queue.qsize() == 2

    fast.queue.get_nowait()
    fast.queue.get_nowait()

    # Third publish — slow's queue is full, so it should be dropped for slow
    # but delivered to fast
    delivered = await broker.publish("ch1", {"n": 3})
    assert delivered == 1  # only fast got it
    assert slow.queue.qsize() == 2  # still full, didn't crash
    assert fast.queue.qsize() == 1


@pytest.mark.asyncio
async def test_stream_async_generator():
    broker = EventBroker()
    sub = await broker.subscribe("ch1")

    async def consumer():
        out = []
        async for m in broker.stream(sub):
            out.append(m)
            if len(out) == 2:
                break
        return out

    task = asyncio.create_task(consumer())
    await broker.publish("ch1", {"i": 1})
    await broker.publish("ch1", {"i": 2})
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == [{"i": 1}, {"i": 2}]


@pytest.mark.asyncio
async def test_close_signals_all_subscribers():
    broker = EventBroker()
    sub = await broker.subscribe("ch1")

    async def consumer():
        out = []
        async for m in broker.stream(sub):
            out.append(m)
        return out

    task = asyncio.create_task(consumer())
    await broker.publish("ch1", {"a": 1})
    await asyncio.sleep(0.05)
    await broker.close()
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result == [{"a": 1}]
