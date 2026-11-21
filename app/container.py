"""Composition root: wires settings, DB, cache, bus, providers and services."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.core.resilience import CircuitBreaker
from app.db.session import make_engine, make_session_factory
from app.db.store import PgChunkStore
from app.events.bus import EventBus, build_bus
from app.ingest.service import IngestService
from app.integrations.bitrix24 import Bitrix24Client
from app.integrations.tracker import EventTracker
from app.providers.embeddings import build_embedder
from app.providers.llm import build_llm
from app.rag.cache import Cache, build_cache
from app.rag.service import RAGService
from app.rag.traces import PgTraceStore
from app.security.auth import Principal, demo_tokens, parse_role_tokens


@dataclass
class Container:
    settings: Settings
    engine: Any
    session_factory: Any
    cache: Cache
    bus: EventBus
    embedder: Any
    llm: Any
    store: PgChunkStore
    ingest: IngestService
    traces: PgTraceStore
    tracker: EventTracker
    rag: RAGService
    bitrix: Bitrix24Client
    tokens: dict[str, Principal] = field(default_factory=dict)
    consumers: list = field(default_factory=list)

    async def close(self) -> None:
        for t in self.consumers:
            t.cancel()
        await self.bus.stop()
        if hasattr(self.cache, "close"):
            await self.cache.close()
        await self.engine.dispose()


def build_container(settings: Settings, *, bus: EventBus | None = None, cache: Cache | None = None) -> Container:
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    cache = cache or build_cache(settings)
    embedder = build_embedder(settings)
    llm = build_llm(settings)
    store = PgChunkStore(sf)
    traces = PgTraceStore(sf)
    tokens = parse_role_tokens(settings.role_tokens)
    if not tokens and settings.demo_mode:
        tokens = demo_tokens()
    return Container(
        settings=settings, engine=engine, session_factory=sf, cache=cache, bus=bus or build_bus(settings), embedder=embedder, llm=llm,
        store=store, ingest=IngestService(sf, embedder, cache), traces=traces, tracker=EventTracker(sf),
        rag=RAGService(settings, store, embedder, llm, cache, traces.write),
        bitrix=Bitrix24Client(settings.b24_webhook_url, dry_run=settings.b24_dry_run, breaker=CircuitBreaker(5, 30)),
        tokens=tokens,
    )
