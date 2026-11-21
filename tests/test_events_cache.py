import asyncio

from app.core.resilience import PermanentError
from app.events.bus import TOPIC_DLQ, TOPIC_INGEST, InMemoryEventBus, process_with_retries
from app.rag.cache import InMemoryCache


async def test_in_memory_bus_delivers_and_buffers_before_subscription():
    bus = InMemoryEventBus()
    await bus.publish(TOPIC_INGEST, "k1", {"n": 1})  # published before any subscriber exists
    got = []

    async def handler(msg):
        got.append(msg["n"])

    await bus.subscribe([TOPIC_INGEST], "g", handler)
    await bus.publish(TOPIC_INGEST, "k2", {"n": 2})
    await bus.join()
    assert got == [1, 2] and bus.topics_published() == [TOPIC_INGEST, TOPIC_INGEST]
    await bus.stop()


async def test_retries_then_dead_letter_queue():
    bus = InMemoryEventBus()
    dlq, failures, slept = [], [], []

    async def sub(msg):
        dlq.append(msg)

    await bus.subscribe([TOPIC_DLQ], "dlq", sub)
    attempts = {"n": 0}

    async def bad(msg):
        attempts["n"] += 1
        raise RuntimeError("boom")

    async def sleep(s):
        slept.append(s)

    async def on_fail(msg, exc):
        failures.append(repr(exc))

    ok = await process_with_retries(bus, TOPIC_INGEST, {"document_id": "d1"}, bad, max_attempts=3, base_delay=0.5, sleep=sleep, on_final_failure=on_fail)
    await bus.join()
    assert not ok and attempts["n"] == 3 and slept == [0.5, 1.0]
    assert dlq[0]["topic"] == TOPIC_INGEST and dlq[0]["attempts"] == 3 and "boom" in dlq[0]["error"] and failures
    await bus.stop()


async def test_success_after_transient_failure_and_permanent_error_skips_retries():
    bus = InMemoryEventBus()
    state = {"n": 0}

    async def flaky(msg):
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("once")

    async def nosleep(s):
        pass

    assert await process_with_retries(bus, "t", {}, flaky, sleep=nosleep) and state["n"] == 2

    async def permanent(msg):
        state["n"] += 100
        raise PermanentError("bad input")

    assert not await process_with_retries(bus, "t", {"id": "x"}, permanent, sleep=nosleep)
    assert state["n"] == 102  # exactly one attempt
    assert bus.published[-1][0] == TOPIC_DLQ


async def test_cache_ttl_idempotency_and_counters():
    clock = {"t": 0.0}
    cache = InMemoryCache(clock=lambda: clock["t"])
    await cache.set_json("k", {"a": [1, 2]}, ttl=10)
    assert await cache.get_json("k") == {"a": [1, 2]}
    clock["t"] = 11
    assert await cache.get_json("k") is None
    assert await cache.set_nx("idem:1", 60) is True
    assert await cache.set_nx("idem:1", 60) is False  # duplicate webhook
    clock["t"] = 100
    assert await cache.set_nx("idem:1", 60) is True
    assert [await cache.incr("rl", ttl=60) for _ in range(3)] == [1, 2, 3]
    await cache.delete("rl")
    assert await cache.incr("rl", ttl=60) == 1
