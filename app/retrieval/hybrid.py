from __future__ import annotations

import asyncio
from dataclasses import replace

from .rrf import rrf_fuse
from .text import term_overlap
from .types import ChunkStore, Hit

MODES = ("hybrid_rrf", "vector_only")


def rerank_by_terms(query: str, hits: list[Hit], weight: float = 0.5) -> list[Hit]:
    """Light reranker: normalised fusion score + weight * key-term overlap."""
    if not hits:
        return hits
    top = max(h.score for h in hits) or 1.0
    for h in hits:
        h.score = h.score / top + weight * h.overlap
    return sorted(hits, key=lambda h: (-h.score, h.chunk_id))


async def search(
    store: ChunkStore,
    *,
    tenant_id: str,
    max_level: int,
    query: str,
    query_embedding: list[float],
    embedding_model: str,
    mode: str = "hybrid_rrf",
    top_k: int = 5,
    rrf_k: int = 60,
    rerank: bool = True,
    candidates: int = 30,
) -> list[Hit]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "vector_only":
        hits = await store.vector_search(tenant_id, max_level, query_embedding, embedding_model, top_k)
    else:
        vec, fts = await asyncio.gather(
            store.vector_search(tenant_id, max_level, query_embedding, embedding_model, candidates),
            store.fts_search(tenant_id, max_level, query, candidates),
        )
        hits = rrf_fuse([vec, fts], k=rrf_k)
        for h in hits:
            h.overlap = term_overlap(query, h.content)
        if rerank:
            hits = rerank_by_terms(query, hits)
        hits = hits[:top_k]
    for h in hits:
        h.overlap = term_overlap(query, h.content)
    return [replace(h) for h in hits]


def is_grounded(hits: list[Hit], min_similarity: float, min_overlap: float) -> bool:
    return any((h.vector_sim or 0.0) >= min_similarity or h.overlap >= min_overlap for h in hits)
