"""Document registration (SHA-256 dedup, versioning) and atomic re-indexing."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.resilience import PermanentError
from app.db.models import Chunk, Document, DocumentBlob
from app.db.store import vector_literal
from app.ingest.parsers import SUPPORTED
from app.ingest.pipeline import build_chunks, sha256_hex
from app.security.auth import RANK

from pathlib import PurePath

_INSERT_CHUNK = text(
    """
    INSERT INTO chunks (id, document_id, tenant_id, access_level, chunk_index, content, page, sheet, section, embedding, embedding_model)
    VALUES (CAST(:id AS uuid), CAST(:document_id AS uuid), :tenant_id, :access_level, :chunk_index, :content, :page, :sheet, :section,
            CAST(:embedding AS vector), :model)
    """
)


@dataclass
class UploadResult:
    document: Document
    action: str  # created | updated | unchanged | duplicate | access_updated | retry
    enqueue: bool


class IngestService:
    def __init__(self, session_factory, embedder, cache=None):
        self._sf, self._embedder, self._cache = session_factory, embedder, cache

    async def _bump_kb_version(self, tenant_id: str) -> None:
        if self._cache is not None:
            await self._cache.incr(f"kbver:{tenant_id}")

    async def register_upload(self, tenant_id: str, filename: str, data: bytes, access_level: str, content_type: str | None = None, role: str | None = None) -> UploadResult:
        filename = PurePath(filename).name
        if PurePath(filename).suffix.lower() not in SUPPORTED:
            raise ValueError(f"unsupported file type; supported: {sorted(SUPPORTED)}")
        if not data:
            raise ValueError("empty file")
        level, sha = RANK[access_level], sha256_hex(data)
        for _ in range(2):
            try:
                return await self._register(tenant_id, filename, data, level, sha, content_type, role)
            except IntegrityError:  # concurrent upload of the same filename: retry sees the winner
                continue
        raise RuntimeError("could not register upload")

    async def _register(self, tenant_id, filename, data, level, sha, content_type, role) -> UploadResult:
        async with self._sf() as s, s.begin():
            doc = (await s.execute(select(Document).where(Document.tenant_id == tenant_id, Document.filename == filename).with_for_update())).scalar_one_or_none()
            if doc is not None:
                if doc.sha256 == sha and doc.status != "failed":
                    if doc.access_level != level:
                        doc.access_level = level
                        await s.execute(update(Chunk).where(Chunk.document_id == doc.id).values(access_level=level))
                        await self._bump_kb_version(tenant_id)
                        return UploadResult(doc, "access_updated", False)
                    return UploadResult(doc, "unchanged", False)
                action = "retry" if doc.sha256 == sha else "updated"
                doc.sha256, doc.size_bytes, doc.access_level = sha, len(data), level
                doc.status, doc.error, doc.content_type, doc.uploaded_by_role = "queued", None, content_type, role
                await s.merge(DocumentBlob(document_id=doc.id, data=data))
                return UploadResult(doc, action, True)
            dup = (await s.execute(select(Document).where(Document.tenant_id == tenant_id, Document.sha256 == sha).limit(1))).scalar_one_or_none()
            if dup is not None:
                return UploadResult(dup, "duplicate", False)
            doc = Document(id=uuid.uuid4(), tenant_id=tenant_id, filename=filename, content_type=content_type, sha256=sha, access_level=level, status="queued", size_bytes=len(data), uploaded_by_role=role)
            s.add(doc)
            await s.flush()
            s.add(DocumentBlob(document_id=doc.id, data=data))
            return UploadResult(doc, "created", True)

    async def index_document(self, document_id: str) -> str:
        doc_uuid = uuid.UUID(str(document_id))
        async with self._sf() as s:
            doc = await s.get(Document, doc_uuid)
            if doc is None:
                return "missing"
            blob = await s.get(DocumentBlob, doc_uuid)
            if blob is None:
                if doc.status == "indexed":
                    return "already_indexed"
                raise PermanentError("uploaded file is no longer available")
            doc.status, doc.error = "processing", None
            filename, data, tenant = doc.filename, blob.data, doc.tenant_id
            await s.commit()
        chunks = await build_chunks(filename, data, self._embedder)  # CPU/IO outside any DB transaction
        sha = sha256_hex(data)
        async with self._sf() as s, s.begin():
            doc = (await s.execute(select(Document).where(Document.id == doc_uuid).with_for_update())).scalar_one_or_none()
            if doc is None or doc.sha256 != sha:
                return "stale"  # a newer upload superseded this one and has its own message
            await s.execute(delete(Chunk).where(Chunk.document_id == doc_uuid))  # old chunks replaced atomically
            await s.execute(
                _INSERT_CHUNK,
                [
                    {
                        "id": str(uuid.uuid4()), "document_id": str(doc_uuid), "tenant_id": tenant, "access_level": doc.access_level,
                        "chunk_index": c.draft.index, "content": c.draft.content, "page": c.draft.page, "sheet": c.draft.sheet,
                        "section": c.draft.section, "embedding": vector_literal(c.embedding), "model": self._embedder.name,
                    }
                    for c in chunks
                ],
            )
            doc.status, doc.chunk_count, doc.error = "indexed", len(chunks), None
            await s.execute(delete(DocumentBlob).where(DocumentBlob.document_id == doc_uuid))
        await self._bump_kb_version(tenant)
        return "indexed"

    async def mark_failed(self, document_id: str, error: str) -> None:
        async with self._sf() as s, s.begin():
            await s.execute(update(Document).where(Document.id == uuid.UUID(str(document_id))).values(status="failed", error=error[:500]))

    async def delete_document(self, tenant_id: str, document_id: str) -> bool:
        async with self._sf() as s, s.begin():
            res = await s.execute(delete(Document).where(Document.id == uuid.UUID(str(document_id)), Document.tenant_id == tenant_id))
        if res.rowcount:
            await self._bump_kb_version(tenant_id)
        return bool(res.rowcount)
