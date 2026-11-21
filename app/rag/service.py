"""RAG orchestration: cache -> embed -> hybrid retrieval (RBAC in SQL) -> grounding check -> LLM -> trace."""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from app.providers.llm import ContextChunk, refusal_for, stream_text
from app.retrieval.hybrid import is_grounded, search
from app.retrieval.types import Hit
from app.security.auth import Principal
from app.security.injection import sanitize_chunk
from app.security.pii import mask_obj, mask_pii

TraceSink = Callable[[dict[str, Any]], Awaitable[str]]


def hit_to_citation(i: int, h: Hit) -> dict[str, Any]:
    return {
        "index": i, "chunk_id": h.chunk_id, "document_id": h.document_id, "filename": h.filename,
        "location": h.location(), "page": h.page, "sheet": h.sheet, "section": h.section,
        "score": round(h.relevance, 3), "text": h.content,
    }


def normalise_question(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


class RAGService:
    def __init__(self, settings, store, embedder, llm, cache, trace_sink: TraceSink):
        self.s, self.store, self.embedder, self.llm, self.cache, self.trace_sink = settings, store, embedder, llm, cache, trace_sink

    async def _cache_key(self, p: Principal, question: str, mode: str, top_k: int) -> str:
        ver = await self.cache.get_json(f"kbver:{p.tenant_id}") or 0
        raw = f"{p.tenant_id}|{p.rank}|{mode}|{top_k}|{self.embedder.name}|{ver}|{normalise_question(question)}"
        return "ans:" + hashlib.sha256(raw.encode()).hexdigest()

    async def retrieve(self, p: Principal, question: str, *, top_k: int = 5, mode: str = "hybrid_rrf") -> list[Hit]:
        vec = (await self.embedder.embed([question]))[0]
        return await search(
            self.store, tenant_id=p.tenant_id, max_level=p.rank, query=question, query_embedding=vec,
            embedding_model=self.embedder.name, mode=mode, top_k=top_k, rrf_k=self.s.rrf_k, rerank=self.s.rerank_enabled,
        )

    async def stream(self, p: Principal, question: str, *, top_k: int = 5, mode: str = "hybrid_rrf", use_cache: bool = True, request_id: str = "") -> AsyncIterator[dict[str, Any]]:
        key = await self._cache_key(p, question, mode, top_k)
        cached = await self.cache.get_json(key) if use_cache else None
        retrieval_ms = generation_ms = 0.0
        injection_flags = 0
        yield {"event": "meta", "data": {"request_id": request_id, "mode": mode, "cache_hit": bool(cached)}}
        if cached:
            result = cached
        else:
            t0 = time.perf_counter()
            hits = await self.retrieve(p, question, top_k=top_k, mode=mode)
            retrieval_ms = (time.perf_counter() - t0) * 1000
            if not hits or not is_grounded(hits, self.s.min_similarity, self.s.min_overlap):
                result = {"answer": refusal_for(question), "citations": [], "refused": True, "provider": "guard", "prompt_tokens": 0, "completion_tokens": 0}
            else:
                contexts = []
                for i, h in enumerate(hits, start=1):
                    clean, removed = sanitize_chunk(h.content)
                    injection_flags += removed
                    contexts.append(ContextChunk(i, h.filename, h.location(), clean))
                t1 = time.perf_counter()
                out = await self.llm.generate(question, contexts)
                generation_ms = (time.perf_counter() - t1) * 1000
                result = {
                    "answer": out.text, "citations": [hit_to_citation(i, h) for i, h in enumerate(hits, start=1)],
                    "refused": out.text.strip() == refusal_for(question), "provider": out.provider,
                    "prompt_tokens": out.prompt_tokens, "completion_tokens": out.completion_tokens,
                }
                if result["refused"]:
                    result["citations"] = []
            if use_cache and not injection_flags:
                await self.cache.set_json(key, result, self.s.cache_ttl_seconds)
        yield {"event": "citations", "data": result["citations"]}
        async for piece in stream_text(result["answer"]):
            yield {"event": "token", "data": piece}
        trace_id = await self.trace_sink(
            {
                "request_id": request_id, "tenant_id": p.tenant_id, "role": p.role,
                "question": mask_pii(question), "answer": mask_pii(result["answer"]), "mode": mode,
                "refused": result["refused"], "cache_hit": bool(cached), "retrieval_ms": round(retrieval_ms, 2),
                "generation_ms": round(generation_ms, 2), "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"], "provider": result["provider"],
                "injection_flags": injection_flags,
                "citations": mask_obj([{**c, "text": c["text"][:400]} for c in result["citations"]]),
            }
        )
        yield {
            "event": "done",
            "data": {"trace_id": trace_id, "request_id": request_id, "cache_hit": bool(cached), "refused": result["refused"], "mode": mode,
                     "retrieval_ms": round(retrieval_ms, 2), "generation_ms": round(generation_ms, 2), "injection_flags": injection_flags},
        }

    async def answer(self, p: Principal, question: str, **kw) -> dict[str, Any]:
        out: dict[str, Any] = {"answer": ""}
        async for ev in self.stream(p, question, **kw):
            if ev["event"] == "token":
                out["answer"] += ev["data"]
            elif ev["event"] == "citations":
                out["citations"] = ev["data"]
            elif ev["event"] == "done":
                out.update(ev["data"])
        return out
