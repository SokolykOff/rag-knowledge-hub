"""Demo knowledge base: two tenants with documents at different access levels (idempotent seeding)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Document

DATA = Path(__file__).parent / "data"

DEMO_DOCS: list[tuple[str, str, str]] = [
    ("acme", "faq_public.md", "guest"),
    ("acme", "catalog.csv", "guest"),
    ("acme", "warranty_policy.pdf", "guest"),
    ("acme", "hr_policy.md", "employee"),
    ("acme", "price_list.xlsx", "employee"),
    ("acme", "contract_DG-2024-17.docx", "manager"),
    ("acme", "salary_matrix.md", "admin"),
    ("globex", "globex_handbook.md", "employee"),
    ("globex", "globex_strategy.md", "admin"),
]


def read_demo(filename: str) -> bytes:
    return (DATA / filename).read_bytes()


async def seed_demo(container) -> dict[str, int]:
    """Index demo docs for every demo tenant that currently has no documents. Safe to call on each start."""
    seeded: dict[str, int] = {}
    async with container.session_factory() as s:
        counts = dict((await s.execute(select(Document.tenant_id, func.count()).group_by(Document.tenant_id))).all())
    for tenant, filename, level in DEMO_DOCS:
        if counts.get(tenant, 0) > 0:
            continue
        res = await container.ingest.register_upload(tenant, filename, read_demo(filename), level, role="admin")
        if res.enqueue:
            await container.ingest.index_document(str(res.document.id))
            seeded[tenant] = seeded.get(tenant, 0) + 1
    return seeded
