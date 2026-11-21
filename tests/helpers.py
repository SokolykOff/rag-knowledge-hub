"""Shared builders for pure (no DB) tests."""
from __future__ import annotations

import uuid
from pathlib import Path

from app.ingest.pipeline import build_chunks
from app.providers.embeddings import LocalHashEmbedder
from app.retrieval.memory import InMemoryChunkStore, MemChunk

DATA = Path(__file__).resolve().parent.parent / "app" / "seed" / "data"
EMB = LocalHashEmbedder()
LEVELS = {"guest": 0, "employee": 1, "manager": 2, "admin": 3}


async def add_file(store: InMemoryChunkStore, tenant: str, filename: str, data: bytes, level: str = "guest") -> int:
    chunks = await build_chunks(filename, data, EMB)
    doc_id = str(uuid.uuid4())
    for c in chunks:
        d = c.draft
        store.add(MemChunk(str(uuid.uuid4()), doc_id, tenant, filename, d.content, LEVELS[level], c.embedding, EMB.name, d.page, d.sheet, d.section))
    return len(chunks)


async def demo_store() -> InMemoryChunkStore:
    store = InMemoryChunkStore()
    plan = [("acme", "faq_public.md", "guest"), ("acme", "catalog.csv", "guest"), ("acme", "warranty_policy.pdf", "guest"), ("acme", "hr_policy.md", "employee"),
            ("acme", "price_list.xlsx", "employee"), ("acme", "contract_DG-2024-17.docx", "manager"), ("acme", "salary_matrix.md", "admin"),
            ("globex", "globex_handbook.md", "employee"), ("globex", "globex_strategy.md", "admin")]
    for tenant, fn, level in plan:
        await add_file(store, tenant, fn, (DATA / fn).read_bytes(), level)
    return store
