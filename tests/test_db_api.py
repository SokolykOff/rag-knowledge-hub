"""Integration tests: need PostgreSQL 17 + pgvector with migrations applied (`alembic upgrade head`).

    DATABASE_URL=postgresql+asyncpg://rag:rag@localhost:5432/rag pytest
"""
import asyncio
import os

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from sqlalchemy import func, select, text  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.models import Chunk, Document  # noqa: E402
from app.events.bus import TOPIC_DLQ  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed.demo import read_demo, seed_demo  # noqa: E402
from app.security.auth import Principal  # noqa: E402

DB = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="DATABASE_URL is not set (PostgreSQL + pgvector required)")


def auth(tenant: str, role: str) -> dict:
    return {"Authorization": f"Bearer demo-{tenant}-{role}"}


@pytest.fixture
async def env():
    settings = Settings(database_url=DB, event_bus="memory", cache_backend="memory", seed_demo=False, demo_mode=True, retry_base_delay=0.01, max_retries=2)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        c = app.state.c
        async with c.session_factory() as s, s.begin():
            await s.execute(text("TRUNCATE documents, document_blobs, chunks, rag_traces, integration_events, audit_logs CASCADE"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, c


async def wait_for(predicate, timeout=15.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached in time")


async def doc_status(client, tenant, role, doc_id):
    r = await client.get(f"/api/v1/documents/{doc_id}", headers=auth(tenant, role))
    return r.json()["status"] if r.status_code == 200 else None


async def test_auth_request_id_and_forbidden_fields(env):
    client, _ = env
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})).status_code == 401
    r = await client.get("/api/v1/me", headers={**auth("acme", "manager"), "X-Request-ID": "trace-me-1"})
    assert r.json() == {"tenant_id": "acme", "role": "manager"} and r.headers["X-Request-ID"] == "trace-me-1"
    r = await client.post("/api/v1/chat", headers=auth("acme", "guest"), json={"question": "hi", "role": "admin", "tenant_id": "globex"})
    assert r.status_code == 422  # identity can never come from the body
    assert (await client.get("/ready")).json() == {"status": "ready"}


async def test_upload_pipeline_statuses_chat_trace_feedback(env):
    client, c = env
    files = {"file": ("policy.md", "# Отпуск\nОтпуск составляет 28 календарных дней.".encode(), "text/markdown")}
    assert (await client.post("/api/v1/documents", headers=auth("acme", "employee"), files=files, data={"access_level": "guest"})).status_code == 403  # employee cannot upload
    r = await client.post("/api/v1/documents", headers=auth("acme", "manager"), files=files, data={"access_level": "employee"})
    assert r.status_code == 202 and r.json()["action"] == "created"
    doc_id = r.json()["id"]
    await wait_for(lambda: _is(doc_status(client, "acme", "manager", doc_id), "indexed"))
    above = await client.post("/api/v1/documents", headers=auth("acme", "manager"), files={"file": ("x.md", b"# a\nb", "text/markdown")}, data={"access_level": "admin"})
    assert above.status_code == 403  # a manager cannot publish an admin-level document

    chat = await client.post("/api/v1/chat", headers={**auth("acme", "employee"), "X-Request-ID": "chat-1"}, json={"question": "Сколько календарных дней отпуска?"})
    body = chat.json()
    assert "28 календарных дней" in body["answer"] and body["citations"][0]["filename"] == "policy.md" and body["request_id"] == "chat-1" and not body["cache_hit"]
    assert (await client.post("/api/v1/chat", headers=auth("acme", "employee"), json={"question": "Сколько календарных дней отпуска?"})).json()["cache_hit"]

    guest = await client.post("/api/v1/chat", headers=auth("acme", "guest"), json={"question": "Сколько календарных дней отпуска?"})
    assert guest.json()["refused"]  # employee-level document is invisible to guest
    assert (await client.get("/api/v1/documents", headers=auth("acme", "guest"))).json() == []
    assert len((await client.get("/api/v1/documents", headers=auth("acme", "employee"))).json()) == 1
    assert (await client.get("/api/v1/documents", headers=auth("globex", "admin"))).json() == []

    traces = (await client.get("/api/v1/traces", headers=auth("acme", "manager"))).json()
    assert len(traces) == 3 and (await client.get("/api/v1/traces", headers=auth("acme", "guest"))).status_code == 403
    tid = body["trace_id"]
    assert (await client.post(f"/api/v1/traces/{tid}/feedback", headers=auth("acme", "employee"), json={"rating": 1})).status_code == 200
    assert (await client.post(f"/api/v1/traces/{tid}/feedback", headers=auth("globex", "admin"), json={"rating": 1})).status_code == 404  # other tenant
    stats = (await client.get("/api/v1/traces/stats", headers=auth("acme", "manager"))).json()
    assert stats["requests"] == 3 and stats["cache_hit_rate"] > 0 and stats["likes"] == 1
    detail = (await client.get(f"/api/v1/traces/{tid}", headers=auth("acme", "manager"))).json()
    assert detail["request_id"] == "chat-1" and detail["retrieval_ms"] >= 0 and detail["citations"]

    async with client.stream("POST", "/api/v1/chat/stream", headers=auth("acme", "employee"), json={"question": "Сколько календарных дней отпуска?", "use_cache": False}) as sse:
        raw = (await sse.aread()).decode()
    assert raw.index("event: meta") < raw.index("event: citations") < raw.index("event: token") < raw.index("event: done")


async def _is(coro, expected):
    return (await coro) == expected


async def test_sha256_idempotency_and_atomic_replace(env):
    _, c = env
    calls = {"n": 0}
    original = c.embedder.embed

    async def counting(texts):
        calls["n"] += 1
        return await original(texts)

    c.embedder.embed = counting
    v1 = b"# Doc\nFirst version about apples."
    r = await c.ingest.register_upload("acme", "a.md", v1, "guest")
    assert r.action == "created" and r.enqueue
    assert await c.ingest.index_document(str(r.document.id)) == "indexed" and calls["n"] == 1

    again = await c.ingest.register_upload("acme", "a.md", v1, "guest")
    assert again.action == "unchanged" and not again.enqueue and calls["n"] == 1  # no re-embedding
    dup = await c.ingest.register_upload("acme", "copy-of-a.md", v1, "guest")
    assert dup.action == "duplicate" and dup.document.id == r.document.id

    upd = await c.ingest.register_upload("acme", "a.md", b"# Doc\nSecond version about oranges.", "guest")
    assert upd.action == "updated" and upd.enqueue
    assert await c.ingest.index_document(str(r.document.id)) == "indexed"
    async with c.session_factory() as s:
        contents = (await s.execute(select(Chunk.content).where(Chunk.document_id == r.document.id))).scalars().all()
        docs = (await s.execute(select(func.count()).select_from(Document))).scalar_one()
    assert docs == 1 and contents and all("oranges" in t and "apples" not in t for t in contents)

    lvl = await c.ingest.register_upload("acme", "a.md", b"# Doc\nSecond version about oranges.", "admin")
    assert lvl.action == "access_updated" and not lvl.enqueue
    async with c.session_factory() as s:
        levels = (await s.execute(select(Chunk.access_level).where(Chunk.document_id == r.document.id))).scalars().all()
    assert set(levels) == {3}


async def test_postgres_hybrid_search_rbac_and_tenants(env):
    _, c = env
    assert await seed_demo(c) == {"acme": 7, "globex": 2}
    assert await seed_demo(c) == {}  # idempotent
    acme_admin, acme_guest = Principal("acme", "admin"), Principal("acme", "guest")

    hits = await c.rag.retrieve(acme_admin, "Остаток по артикулу CB-77120", top_k=5)
    assert any("CB-77120" in h.content for h in hits[:2])
    hits = await c.rag.retrieve(acme_admin, "срок оплаты по договору ДГ-2024/17", top_k=5)
    assert any("30 календарных дней" in h.content for h in hits[:3])
    vec_only = await c.rag.retrieve(acme_admin, "гарантия на ноутбуки", top_k=5, mode="vector_only")
    assert vec_only and all(h.vector_sim is not None for h in vec_only)

    guest_hits = await c.rag.retrieve(acme_guest, "оклад Senior грейд", top_k=10)
    assert all(h.access_level == 0 for h in guest_hits) and not any("300 000" in h.content for h in guest_hits)
    assert not any("Orion" in h.content for h in await c.rag.retrieve(acme_admin, "запуск продукта Orion бюджет", top_k=10))
    assert any("Orion" in h.content for h in await c.rag.retrieve(Principal("globex", "admin"), "запуск продукта Orion бюджет", top_k=10))

    pdf_chunks = await c.rag.retrieve(acme_guest, "гарантийный срок ноутбуки 24 месяца", top_k=5)
    assert any(h.page for h in pdf_chunks)  # PDF page numbers survive the whole pipeline


async def test_bad_file_ends_in_failed_status_and_dlq(env):
    client, c = env
    files = {"file": ("broken.pdf", b"%PDF-1.4 this is not a real pdf", "application/pdf")}
    r = await client.post("/api/v1/documents", headers=auth("acme", "manager"), files=files, data={"access_level": "guest"})
    doc_id = r.json()["id"]
    await wait_for(lambda: _is(doc_status(client, "acme", "manager", doc_id), "failed"))
    assert TOPIC_DLQ in c.bus.topics_published()
    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=auth("acme", "manager"))).json()["error"]
    assert (await client.post("/api/v1/documents", headers=auth("acme", "manager"), files={"file": ("a.exe", b"MZ", "application/octet-stream")}, data={"access_level": "guest"})).status_code == 422


async def test_bitrix24_webhook_token_idempotency_and_worker_flow(env):
    client, c = env
    await seed_demo(c)
    payload = {"event": "ONCRMLEADADD", "data": {"FIELDS": {"ID": "501", "TITLE": "Нужна цена на SKU-48213", "COMMENTS": "Сколько стоит и какая гарантия на ноутбук?"}}, "auth": {"application_token": "demo-b24-token"}}
    url = "/api/v1/integrations/bitrix24/webhook"
    bad = {**payload, "auth": {"application_token": "wrong"}}
    assert (await client.post(url, json=bad)).status_code == 401

    first = await client.post(url, json=payload, headers={"Idempotency-Key": "evt-1"})
    assert first.status_code == 202 and first.json()["status"] == "accepted"
    second = await client.post(url, json=payload, headers={"Idempotency-Key": "evt-1"})
    assert second.json() == {"status": "duplicate", "idempotency_key": "evt-1"}
    assert c.bus.topics_published().count("rag.bitrix24.events") == 1

    async def done():
        ev = (await client.get("/api/v1/integrations/events", headers=auth("acme", "manager"))).json()
        return ev[0] if ev and ev[0]["status"] == "done" else None

    event = await wait_for(done)
    steps = [s["step"] for s in event["steps"]]
    assert steps[0] == "webhook_received" and "published:rag.bitrix24.events" in steps and "consumed:rag.bitrix24.events" in steps and any("dry-run" in s for s in steps)
    assert "[AI-черновик" in event["result"]["comment"] and "token" not in str(event["steps"]).lower()

    form = await client.post(url, data={"event": "ONCRMLEADADD", "data[FIELDS][ID]": "502", "data[FIELDS][TITLE]": "Гарантия на ноутбуки", "auth[application_token]": "demo-b24-token"}, headers={"Idempotency-Key": "evt-2"})
    assert form.status_code == 202  # native Bitrix24 form encoding is accepted too

    sim = await client.post("/api/v1/integrations/bitrix24/simulate", headers=auth("acme", "manager"), json={})
    assert sim.status_code == 202 and (await client.post("/api/v1/integrations/bitrix24/simulate", headers=auth("acme", "guest"), json={})).status_code == 403


async def test_eval_endpoint_runs_both_modes(env):
    client, _ = env
    assert (await client.post("/api/v1/eval/run", headers=auth("acme", "employee"))).status_code == 403
    r = await client.post("/api/v1/eval/run", headers=auth("acme", "manager"))
    assert r.status_code == 200
    res = r.json()
    assert res["hybrid_rrf"]["recall_at_5"] >= 0.8 and 0 <= res["vector_only"]["mrr"] <= 1 and res["hybrid_rrf"]["avg_latency_ms"] >= 0
    assert (await client.get("/api/v1/documents", headers=auth("acme", "admin"))).json() == []  # eval corpus lives in its own tenant
