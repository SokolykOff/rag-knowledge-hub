"""Needs a live Redis: REDIS_URL=redis://localhost:6379/0 pytest tests/test_redis_cache.py"""
import asyncio
import os
import uuid

import pytest

pytest.importorskip("redis")
pytestmark = pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="REDIS_URL is not set")


async def test_redis_cache_roundtrip_idempotency_ttl_and_counter():
    from app.rag.cache import RedisCache

    cache = RedisCache(os.environ["REDIS_URL"])
    ns = f"test:{uuid.uuid4().hex}"
    try:
        assert await cache.ping()
        await cache.set_json(f"{ns}:k", {"answer": "да", "citations": [1]}, ttl=30)
        assert await cache.get_json(f"{ns}:k") == {"answer": "да", "citations": [1]}
        assert await cache.get_json(f"{ns}:missing") is None
        assert await cache.set_nx(f"{ns}:idem", 30) is True
        assert await cache.set_nx(f"{ns}:idem", 30) is False  # webhook duplicate
        assert [await cache.incr(f"{ns}:rl", ttl=30) for _ in range(3)] == [1, 2, 3]
        await cache.set_json(f"{ns}:short", {"x": 1}, ttl=1)
        await asyncio.sleep(1.5)
        assert await cache.get_json(f"{ns}:short") is None
        await cache.delete(f"{ns}:idem")
        assert await cache.set_nx(f"{ns}:idem", 30) is True
    finally:
        await cache.close()
