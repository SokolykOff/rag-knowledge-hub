from app.eval.dataset import QUESTIONS, large_catalog_csv
from app.eval.runner import run_eval
from app.retrieval.hybrid import search

from .helpers import EMB, add_file, demo_store


async def test_eval_runner_compares_modes():
    store = await demo_store()
    await add_file(store, "acme", "eval_catalog.csv", large_catalog_csv(), "guest")

    async def search_fn(q, mode):
        vec = (await EMB.embed([q]))[0]
        return await search(store, tenant_id="acme", max_level=3, query=q, query_embedding=vec, embedding_model=EMB.name, mode=mode, top_k=5)

    res = await run_eval(search_fn, QUESTIONS, k=5)
    assert res["questions"] == len(QUESTIONS) and set(res) >= {"vector_only", "hybrid_rrf", "delta"}
    for mode in ("vector_only", "hybrid_rrf"):
        m = res[mode]
        assert 0 <= m["recall_at_5"] <= 1 and 0 <= m["mrr"] <= 1 and m["avg_latency_ms"] >= 0 and len(m["details"]) == len(QUESTIONS)
    assert res["hybrid_rrf"]["recall_at_5"] >= 0.9
    assert res["hybrid_rrf"]["mrr"] >= res["vector_only"]["mrr"]  # hybrid must not be worse than vector-only on this dataset
