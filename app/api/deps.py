from __future__ import annotations

import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container
from app.security.auth import RANK, Principal, authenticate

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.c


async def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), c: Container = Depends(get_container)) -> Principal:
    p = authenticate(creds.credentials if creds else None, c.tokens)
    if p is None:
        raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return p


def require_role(minimum: str):
    async def dep(p: Principal = Depends(get_principal)) -> Principal:
        if p.rank < RANK[minimum]:
            raise HTTPException(403, f"requires role >= {minimum}")
        return p

    return dep


async def rate_limit(p: Principal = Depends(get_principal), c: Container = Depends(get_container)) -> Principal:
    window = int(time.time() // 60)
    n = await c.cache.incr(f"rl:{p.tenant_id}:{p.role}:{window}", ttl=70)
    if n > c.settings.rate_limit_per_min:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(60 - int(time.time() % 60))})
    return p
