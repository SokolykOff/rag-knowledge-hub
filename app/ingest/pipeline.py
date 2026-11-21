"""parse -> chunk -> embed. Pure of any database code, shared by workers, seeding and eval."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .chunking import ChunkDraft, chunk_segments
from .parsers import parse_document


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class EmbeddedChunk:
    draft: ChunkDraft
    embedding: list[float]


async def build_chunks(filename: str, data: bytes, embedder) -> list[EmbeddedChunk]:
    segments = parse_document(filename, data)
    drafts = chunk_segments(segments)
    if not drafts:
        raise ValueError("document contains no extractable text")
    vectors = await embedder.embed([d.content for d in drafts])
    return [EmbeddedChunk(d, v) for d, v in zip(drafts, vectors)]
