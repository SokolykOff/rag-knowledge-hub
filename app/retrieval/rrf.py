from __future__ import annotations

from .types import Hit


def rrf_fuse(rankings: list[list[Hit]], k: int = 60, limit: int | None = None) -> list[Hit]:
    """Reciprocal Rank Fusion: score(d) = sum_i 1 / (k + rank_i(d)), ranks start at 1."""
    fused: dict[str, Hit] = {}
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            fused[hit.chunk_id] = fused[hit.chunk_id].merged_with(hit) if hit.chunk_id in fused else hit
    ordered = sorted(fused.values(), key=lambda h: (-scores[h.chunk_id], h.chunk_id))
    for h in ordered:
        h.score = scores[h.chunk_id]
    return ordered[:limit] if limit else ordered
