"""Cache abstraction: answers cache, webhook idempotency (SET NX) and fixed-window rate limits."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol


class Cache(Protocol):
    async def get_json(self, key: str) -> Any | None: ...
    async def set_json(self, key: str, value: Any, ttl: int) -> None: ...
    async def set_nx(self, key: str, ttl: int) -> bool: ...
    async def incr(self, key: str, ttl: int | None = None) -> int: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...


class InMemoryCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._d: dict[str, tuple[Any, float | None]] = {}
        self._clock = clock

    def _live(self, key: str):
        item = self._d.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and exp <= self._clock():
            del self._d[key]
            return None
        return item

    async def get_json(self, key):
        item = self._live(key)
        return json.loads(item[0]) if item else None

    async def set_json(self, key, value, ttl):
        self._d[key] = (json.dumps(value, ensure_ascii=False), self._clock() + ttl)

    async def set_nx(self, key, ttl):
        if self._live(key):
            return False
        self._d[key] = ("1", self._clock() + ttl)
        return True

    async def incr(self, key, ttl=None):
        item = self._live(key)
        if item is None:
            self._d[key] = ("1", self._clock() + ttl if ttl else None)
            return 1
        n = int(item[0]) + 1
        self._d[key] = (str(n), item[1])
        return n

    async def delete(self, key):
        self._d.pop(key, None)

    async def ping(self):
        return True


class RedisCache:
    def __init__(self, url: str):
        import redis.asyncio as redis

        self._r = redis.from_url(url, decode_responses=True)

    async def get_json(self, key):
        raw = await self._r.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key, value, ttl):
        await self._r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)

    async def set_nx(self, key, ttl):
        return bool(await self._r.set(key, "1", nx=True, ex=ttl))

    async def incr(self, key, ttl=None):
        n = await self._r.incr(key)
        if ttl and n == 1:
            await self._r.expire(key, ttl)
        return int(n)

    async def delete(self, key):
        await self._r.delete(key)

    async def ping(self):
        return bool(await self._r.ping())

    async def close(self):
        await self._r.aclose()


def build_cache(settings) -> Cache:
    return InMemoryCache() if settings.cache_backend == "memory" else RedisCache(settings.redis_url)
