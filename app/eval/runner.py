"""Retrieval quality benchmark: vector_only vs hybrid_rrf -> Recall@5, MRR, mean latency."""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from app.retrieval.types import Hit

from .dataset import QUESTIONS, EvalQuestion

SearchFn = Callable[[str, str], Awaitable[list[Hit]]]  # (question, mode) -> hits


def is_relevant(hit: Hit, q: EvalQuestion) -> bool:
    low = hit.content.lower()
    return any(m.lower() in low for m in q.must_contain)


async def run_mode(search_fn: SearchFn, mode: str, questions: list[EvalQuestion], k: int = 5) -> dict:
    recalls, rrs, latencies, details = [], [], [], []
    for q in questions:
        t0 = time.perf_counter()
        hits = (await search_fn(q.question, mode))[:k]
        latencies.append((time.perf_counter() - t0) * 1000)
        rank = next((i for i, h in enumerate(hits, start=1) if is_relevant(h, q)), None)
        recalls.append(1.0 if rank else 0.0)
        rrs.append(1.0 / rank if rank else 0.0)
        details.append({"question": q.question, "kind": q.kind, "first_relevant_rank": rank, "top": hits[0].filename if hits else None})
    n = len(questions) or 1
    return {"mode": mode, "recall_at_5": round(sum(recalls) / n, 4), "mrr": round(sum(rrs) / n, 4), "avg_latency_ms": round(sum(latencies) / n, 2), "details": details}


async def run_eval(search_fn: SearchFn, questions: list[EvalQuestion] | None = None, k: int = 5) -> dict:
    questions = questions or QUESTIONS
    vec = await run_mode(search_fn, "vector_only", questions, k)
    hyb = await run_mode(search_fn, "hybrid_rrf", questions, k)
    return {
        "questions": len(questions), "k": k, "vector_only": vec, "hybrid_rrf": hyb,
        "delta": {"recall_at_5": round(hyb["recall_at_5"] - vec["recall_at_5"], 4), "mrr": round(hyb["mrr"] - vec["mrr"], 4)},
    }
