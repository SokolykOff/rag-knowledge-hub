from app.retrieval.hybrid import is_grounded, search
from app.retrieval.rrf import rrf_fuse
from app.retrieval.types import Hit

from .helpers import EMB, demo_store


def H(cid: str) -> Hit:
    return Hit(cid, "d", "f.md", cid)


def test_rrf_math_and_order():
    fused = rrf_fuse([[H("a"), H("b"), H("c")], [H("c"), H("a"), H("d")]], k=60)
    scores = {h.chunk_id: h.score for h in fused}
    assert abs(scores["a"] - (1 / 61 + 1 / 62)) < 1e-12
    assert abs(scores["c"] - (1 / 63 + 1 / 61)) < 1e-12
    assert [h.chunk_id for h in fused][:2] == ["a", "c"]  # present in both lists beats single-list hits
    assert {h.chunk_id for h in fused} == {"a", "b", "c", "d"}


def test_rrf_merges_signals_from_both_rankers():
    v, f = H("x"), H("x")
    v.vector_sim, f.fts_rank = 0.7, 3.2
    fused = rrf_fuse([[v], [f]])
    assert fused[0].vector_sim == 0.7 and fused[0].fts_rank == 3.2


async def _q(store, text, tenant="acme", level=3, mode="hybrid_rrf", k=5):
    vec = (await EMB.embed([text]))[0]
    return await search(store, tenant_id=tenant, max_level=level, query=text, query_embedding=vec, embedding_model=EMB.name, mode=mode, top_k=k)


async def test_hybrid_finds_article_codes_and_contract_numbers():
    store = await demo_store()
    hits = await _q(store, "Остаток по артикулу CB-77120")
    assert "CB-77120" in hits[0].content
    hits = await _q(store, "срок оплаты по договору ДГ-2024/17")
    assert any("30 календарных дней" in h.content for h in hits[:3])


async def test_pre_retrieval_rbac_levels():
    store = await demo_store()
    guest = await _q(store, "оклад Senior грейд", level=0)
    assert all(h.access_level == 0 for h in guest)
    assert not any("300 000" in h.content for h in guest)
    admin = await _q(store, "оклад Senior грейд", level=3)
    assert any("300 000" in h.content for h in admin)
    emp = await _q(store, "договор ДГ-2024/17 неустойка", level=1)
    assert not any("0,1%" in h.content for h in emp)  # manager-level contract is invisible to employee


async def test_tenant_isolation():
    store = await demo_store()
    hits = await _q(store, "запуск продукта Orion бюджет", tenant="acme", level=3)
    assert not any("Orion" in h.content for h in hits)
    hits = await _q(store, "запуск продукта Orion бюджет", tenant="globex", level=3)
    assert any("Orion" in h.content for h in hits)
    hits = await _q(store, "запуск продукта Orion бюджет", tenant="globex", level=1)
    assert not any("Orion" in h.content for h in hits)


async def test_grounding_threshold_refuses_offtopic():
    store = await demo_store()
    for q in ["Какая погода на Марсе завтра?", "Расскажи рецепт борща", "Who won the 2018 world cup?"]:
        assert not is_grounded(await _q(store, q), 0.22, 0.34), q
    assert is_grounded(await _q(store, "Сколько дней отпуска?"), 0.22, 0.34)


async def test_vector_only_mode_and_unknown_mode():
    store = await demo_store()
    hits = await _q(store, "гарантия на ноутбуки", mode="vector_only")
    assert hits and all(h.vector_sim is not None for h in hits)
    try:
        await _q(store, "x", mode="bogus")
    except ValueError:
        return
    raise AssertionError("unknown mode must raise")
