"""PostgreSQL ChunkStore. RBAC is enforced in SQL *before* any row is selected:
`tenant_id = :tenant AND access_level <= :max_level` is part of every WHERE clause."""
from __future__ import annotations

from sqlalchemy import text

from app.retrieval.text import code_tokens
from app.retrieval.types import Hit

_VECTOR_SQL = text(
    """
    SELECT * FROM (
        SELECT c.id::text AS chunk_id, c.document_id::text AS document_id, d.filename, c.content,
               c.page, c.sheet, c.section, c.access_level,
               1 - (c.embedding <=> CAST(:emb AS vector)) AS sim,
               c.embedding <=> CAST(:emb AS vector) AS dist
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tenant AND c.access_level <= :max_level AND c.embedding_model = :model
        ORDER BY c.embedding <=> CAST(:emb AS vector)
        LIMIT :limit
    ) t ORDER BY dist
    """
)

_FTS_SQL = text(
    """
    WITH q AS (
        SELECT (replace(plainto_tsquery('russian', :q)::text, '&', '|')::tsquery
             || replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery) AS tq
    )
    SELECT c.id::text AS chunk_id, c.document_id::text AS document_id, d.filename, c.content,
           c.page, c.sheet, c.section, c.access_level,
           ts_rank_cd(c.tsv, q.tq, 1)
             + CASE WHEN c.content ILIKE ANY(CAST(:codes AS text[])) THEN 1.0 ELSE 0.0 END AS rank
    FROM chunks c JOIN documents d ON d.id = c.document_id, q
    WHERE c.tenant_id = :tenant AND c.access_level <= :max_level
      AND (c.tsv @@ q.tq OR c.content ILIKE ANY(CAST(:codes AS text[])))
    ORDER BY rank DESC, c.id
    LIMIT :limit
    """
)


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _like(code: str) -> str:
    return "%" + code.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _hit(row, **kw) -> Hit:
    return Hit(row.chunk_id, row.document_id, row.filename, row.content, row.page, row.sheet, row.section, row.access_level, **kw)


class PgChunkStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def vector_search(self, tenant_id, max_level, embedding, model, limit):
        async with self._sf() as s:
            # Recall with filters on an approximate HNSW index. `iterative_scan` exists only in pgvector >= 0.8,
            # so each tuning call runs in its own SAVEPOINT and a failure cannot abort the search transaction.
            for sql in ("SELECT set_config('hnsw.ef_search', '100', true)", "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"):
                try:
                    async with s.begin_nested():
                        await s.execute(text(sql))
                except Exception:  # noqa: BLE001
                    pass
            rows = (await s.execute(_VECTOR_SQL, {"emb": vector_literal(embedding), "tenant": tenant_id, "max_level": max_level, "model": model, "limit": limit})).all()
        return [_hit(r, vector_sim=float(r.sim), score=float(r.sim)) for r in rows]

    async def fts_search(self, tenant_id, max_level, query, limit):
        codes = [_like(c) for c in code_tokens(query)]
        async with self._sf() as s:
            rows = (await s.execute(_FTS_SQL, {"q": query, "codes": codes, "tenant": tenant_id, "max_level": max_level, "limit": limit})).all()
        return [_hit(r, fts_rank=float(r.rank), score=float(r.rank)) for r in rows]
