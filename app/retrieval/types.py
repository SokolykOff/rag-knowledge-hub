from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    page: int | None = None
    sheet: str | None = None
    section: str | None = None
    access_level: int = 0
    score: float = 0.0  # fused / final ordering score
    vector_sim: float | None = None  # cosine similarity if found by vector search
    fts_rank: float | None = None  # ts_rank if found by FTS
    overlap: float = 0.0  # keyword overlap with the query (0..1)

    @property
    def relevance(self) -> float:
        return max(self.vector_sim or 0.0, self.overlap)

    def location(self) -> str:
        if self.page is not None:
            return f"стр. {self.page}"
        if self.sheet:
            return f"лист «{self.sheet}»"
        return self.section or ""

    def merged_with(self, other: "Hit") -> "Hit":
        return replace(
            self,
            vector_sim=self.vector_sim if self.vector_sim is not None else other.vector_sim,
            fts_rank=self.fts_rank if self.fts_rank is not None else other.fts_rank,
        )


class ChunkStore(Protocol):
    """Pre-retrieval RBAC contract: tenant and access level are filtered INSIDE the query."""

    async def vector_search(self, tenant_id: str, max_level: int, embedding: list[float], model: str, limit: int) -> list[Hit]: ...

    async def fts_search(self, tenant_id: str, max_level: int, query: str, limit: int) -> list[Hit]: ...
