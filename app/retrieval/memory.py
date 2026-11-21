"""In-memory ChunkStore with the same RBAC contract as PgChunkStore (tests, eval, demos without DB)."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .text import code_tokens, keyword_stems
from .types import Hit


@dataclass
class MemChunk:
    chunk_id: str
    document_id: str
    tenant_id: str
    filename: str
    content: str
    access_level: int
    embedding: list[float]
    embedding_model: str
    page: int | None = None
    sheet: str | None = None
    section: str | None = None


class InMemoryChunkStore:
    def __init__(self) -> None:
        self.chunks: list[MemChunk] = []

    def add(self, chunk: MemChunk) -> None:
        self.chunks.append(chunk)

    def _visible(self, tenant_id: str, max_level: int):
        return [c for c in self.chunks if c.tenant_id == tenant_id and c.access_level <= max_level]

    @staticmethod
    def _hit(c: MemChunk, **kw) -> Hit:
        return Hit(c.chunk_id, c.document_id, c.filename, c.content, c.page, c.sheet, c.section, c.access_level, **kw)

    async def vector_search(self, tenant_id, max_level, embedding, model, limit):
        scored = []
        for c in self._visible(tenant_id, max_level):
            if c.embedding_model != model:
                continue
            sim = sum(a * b for a, b in zip(embedding, c.embedding))
            scored.append((sim, c))
        scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
        return [self._hit(c, vector_sim=sim, score=sim) for sim, c in scored[:limit]]

    async def fts_search(self, tenant_id, max_level, query, limit):
        pool = self._visible(tenant_id, max_level)
        q_terms = set(keyword_stems(query))
        codes = code_tokens(query)
        if not pool or not (q_terms or codes):
            return []
        docs = [Counter(keyword_stems(c.content)) for c in pool]
        avg = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
        df = Counter(t for d in docs for t in d if t in q_terms)
        scored = []
        for c, d in zip(pool, docs):
            length = sum(d.values()) or 1
            score = 0.0
            for t in q_terms:
                if t in d:
                    idf = math.log(1 + (len(pool) - df[t] + 0.5) / (df[t] + 0.5))
                    tf = d[t]
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
            low = c.content.lower()
            if any(code in low for code in codes):
                score += 5.0
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
        return [self._hit(c, fts_rank=s, score=s) for s, c in scored[:limit]]
