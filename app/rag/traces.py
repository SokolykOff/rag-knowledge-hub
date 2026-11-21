from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.db.models import RagTrace


def trace_to_dict(t: RagTrace, full: bool = True) -> dict[str, Any]:
    d = {
        "id": str(t.id), "request_id": t.request_id, "tenant_id": t.tenant_id, "role": t.role, "question": t.question,
        "mode": t.mode, "refused": t.refused, "cache_hit": t.cache_hit, "retrieval_ms": t.retrieval_ms,
        "generation_ms": t.generation_ms, "prompt_tokens": t.prompt_tokens, "completion_tokens": t.completion_tokens,
        "provider": t.provider, "injection_flags": t.injection_flags, "feedback": t.feedback,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if full:
        d.update(answer=t.answer, citations=t.citations or [], feedback_comment=t.feedback_comment)
    return d


class PgTraceStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def write(self, trace: dict[str, Any]) -> str:
        async with self._sf() as s, s.begin():
            row = RagTrace(**trace)
            s.add(row)
            await s.flush()
            return str(row.id)

    async def list(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = (await s.execute(select(RagTrace).where(RagTrace.tenant_id == tenant_id).order_by(RagTrace.created_at.desc()).limit(limit))).scalars().all()
        return [trace_to_dict(r, full=False) for r in rows]

    async def get(self, tenant_id: str, trace_id: str) -> dict[str, Any] | None:
        async with self._sf() as s:
            row = (await s.execute(select(RagTrace).where(RagTrace.id == uuid.UUID(trace_id), RagTrace.tenant_id == tenant_id))).scalar_one_or_none()
        return trace_to_dict(row) if row else None

    async def set_feedback(self, tenant_id: str, trace_id: str, rating: int, comment: str | None) -> bool:
        async with self._sf() as s, s.begin():
            row = (await s.execute(select(RagTrace).where(RagTrace.id == uuid.UUID(trace_id), RagTrace.tenant_id == tenant_id))).scalar_one_or_none()
            if row is None:
                return False
            row.feedback, row.feedback_comment = rating, (comment or None)
            return True

    async def stats(self, tenant_id: str) -> dict[str, Any]:
        async with self._sf() as s:
            r = (await s.execute(
                select(
                    func.count(), func.count().filter(RagTrace.cache_hit), func.count().filter(RagTrace.refused),
                    func.avg(RagTrace.retrieval_ms + RagTrace.generation_ms),
                    func.count().filter(RagTrace.feedback == 1), func.count().filter(RagTrace.feedback == -1),
                    func.coalesce(func.sum(RagTrace.prompt_tokens + RagTrace.completion_tokens), 0),
                ).where(RagTrace.tenant_id == tenant_id)
            )).one()
        total, hits, refused, avg_ms, likes, dislikes, tokens = r
        rated = likes + dislikes
        return {
            "requests": total, "cache_hit_rate": round(hits / total, 3) if total else 0.0, "refusal_rate": round(refused / total, 3) if total else 0.0,
            "avg_latency_ms": round(float(avg_ms or 0), 1), "likes": likes, "dislikes": dislikes,
            "rating": round(likes / rated, 3) if rated else None, "tokens": int(tokens),
        }
