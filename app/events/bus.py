"""EventBus abstraction with Kafka (aiokafka) and in-memory implementations + retry/DLQ wrapper."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Protocol

from app.core.resilience import PermanentError

log = logging.getLogger("rag.bus")

TOPIC_INGEST = "rag.documents.ingest"
TOPIC_BITRIX = "rag.bitrix24.events"
TOPIC_DLQ = "rag.dlq"
ALL_TOPICS = (TOPIC_INGEST, TOPIC_BITRIX, TOPIC_DLQ)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None: ...
    async def subscribe(self, topics: list[str], group: str, handler: Handler) -> asyncio.Task: ...


class InMemoryEventBus:
    """Single-process bus for unit tests and broker-less runs. Messages published before a
    subscriber exists are buffered, so start order does not matter."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._backlog: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        self.published.append((topic, key, value))
        queues = self._queues[topic]
        if not queues:
            self._backlog[topic].append((key, value))
            return
        for q in queues:
            q.put_nowait(value)

    async def subscribe(self, topics: list[str], group: str, handler: Handler) -> asyncio.Task:
        q: asyncio.Queue = asyncio.Queue()
        for topic in topics:
            self._queues[topic].append(q)
            for _, value in self._backlog.pop(topic, []):
                q.put_nowait(value)

        async def loop():
            while True:
                msg = await q.get()
                try:
                    await handler(msg)
                except Exception:  # noqa: BLE001 - handlers are expected to wrap errors themselves
                    log.exception("handler crashed")
                finally:
                    q.task_done()

        task = asyncio.create_task(loop())
        task._queue = q  # type: ignore[attr-defined]
        self._tasks.append(task)
        return task

    async def join(self) -> None:
        """Wait until every subscribed queue is drained (tests)."""
        for _ in range(3):  # handlers may publish follow-up messages
            await asyncio.gather(*(t._queue.join() for t in self._tasks))  # type: ignore[attr-defined]

    def topics_published(self) -> list[str]:
        return [t for t, _, _ in self.published]


class KafkaEventBus:
    def __init__(self, bootstrap: str, client_id: str = "rag-hub") -> None:
        self.bootstrap, self.client_id = bootstrap, client_id
        self._producer = None
        self._consumers: list = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer
        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.errors import TopicAlreadyExistsError

        admin = AIOKafkaAdminClient(bootstrap_servers=self.bootstrap, client_id=self.client_id)
        await admin.start()
        try:
            await admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1) for t in ALL_TOPICS])
        except TopicAlreadyExistsError:
            pass
        finally:
            await admin.close()
        self._producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap, client_id=self.client_id, acks="all")
        await self._producer.start()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for c in self._consumers:
            await c.stop()
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, key: str, value: dict[str, Any]) -> None:
        assert self._producer is not None, "bus not started"
        await self._producer.send_and_wait(topic, json.dumps(value, ensure_ascii=False).encode(), key=key.encode())

    async def subscribe(self, topics: list[str], group: str, handler: Handler) -> asyncio.Task:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(*topics, bootstrap_servers=self.bootstrap, group_id=group, enable_auto_commit=False, auto_offset_reset="earliest")
        await consumer.start()
        self._consumers.append(consumer)

        async def loop():
            async for msg in consumer:
                try:
                    await handler(json.loads(msg.value))
                except Exception:  # noqa: BLE001
                    log.exception("handler crashed")
                await consumer.commit()  # at-least-once: commit after handling (failures already routed to DLQ)

        task = asyncio.create_task(loop())
        self._tasks.append(task)
        return task


def build_bus(settings) -> EventBus:
    return KafkaEventBus(settings.kafka_bootstrap) if settings.event_bus == "kafka" else InMemoryEventBus()


async def process_with_retries(
    bus: EventBus,
    topic: str,
    message: dict[str, Any],
    handler: Handler,
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_final_failure: Callable[[dict[str, Any], Exception], Awaitable[None]] | None = None,
) -> bool:
    """Run handler with exponential backoff; after the last failure publish to rag.dlq."""
    last: Exception | None = None
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            await handler(message)
            return True
        except PermanentError as exc:
            last = exc
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < max_attempts:
                await sleep(base_delay * 2 ** (attempt - 1))
    assert last is not None
    await bus.publish(TOPIC_DLQ, str(message.get("id") or message.get("document_id") or "unknown"), {"topic": topic, "payload": message, "error": repr(last)[:500], "attempts": attempts})
    if on_final_failure:
        await on_final_failure(message, last)
    return False
