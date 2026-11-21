from __future__ import annotations

import re
from dataclasses import dataclass

from .parsers import Segment

_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ChunkDraft:
    index: int
    content: str
    page: int | None = None
    sheet: str | None = None
    section: str | None = None


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split by paragraphs, then sentences, then hard-wrap; adjacent pieces overlap slightly."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        for sent in _SENT.split(para):
            while len(sent) > max_chars:
                pieces.append(sent[:max_chars])
                sent = sent[max_chars - overlap :]
            if sent.strip():
                pieces.append(sent.strip())
    out: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > max_chars:
            out.append(cur)
            tail = cur[-overlap:] if overlap else ""
            cur = (tail.split(" ", 1)[-1] + " " + p).strip() if tail and " " in tail else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def chunk_segments(segments: list[Segment], max_chars: int = 900, overlap: int = 100) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if seg.kind == "table":
            if len(text) <= max_chars * 2:
                parts = [text]
            else:  # very wide rows: keep the header line on every part
                head, _, rest = text.partition("\n")
                parts = [f"{head}\n{p}" for p in _split_long(rest, max_chars, 0)]
        else:
            parts = _split_long(text, max_chars, overlap)
            if seg.section:
                parts = [f"{seg.section}\n{p}" for p in parts]
        for part in parts:
            drafts.append(ChunkDraft(len(drafts), part, seg.page, seg.sheet, seg.section))
    return drafts
