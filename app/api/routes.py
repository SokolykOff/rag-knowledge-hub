from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import get_container, get_principal, rate_limit, require_role
from app.api.schemas import ChatRequest, FeedbackRequest, SimulateLeadRequest
from app.container import Container
from app.db.models import AuditLog, Chunk, Document
from app.eval.dataset import CORPUS, EVAL_TENANT, EXTRA_FILES, QUESTIONS
from app.eval.runner import run_eval
from app.events.bus import TOPIC_BITRIX, TOPIC_INGEST
from app.integrations.bitrix24 import derive_idempotency_key, expand_bracket_keys, extract_event, simulated_lead_payload, verify_application_token
from app.security.auth import RANK, ROLES, Principal
from app.security.pii import mask_obj
from app.seed.demo import read_demo

router = APIRouter(prefix="/api/v1")


def _doc(d: Document) -> dict[str, Any]:
    return {
        "id": str(d.id), "filename": d.filename, "status": d.status, "error": d.error, "access_level": ROLES[d.access_level],
        "size_bytes": d.size_bytes, "chunk_count": d.chunk_count, "sha256": d.sha256,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


async def audit(c: Container, request: Request, p: Principal, action: str, details: dict[str, Any]) -> None:
    async with c.session_factory() as s, s.begin():
        s.add(AuditLog(request_id=getattr(request.state, "request_id", None), tenant_id=p.tenant_id, role=p.role, action=action, details=mask_obj(details)))


# ------------------------------------------------------------------ identity
@router.get("/me")
async def me(p: Principal = Depends(get_principal)):
    return {"tenant_id": p.tenant_id, "role": p.role}


@router.get("/demo/identities")
async def demo_identities(c: Container = Depends(get_container)):
    if not c.settings.demo_mode:
        raise HTTPException(404, "demo mode is disabled")
    return [{"token": t, "tenant_id": p.tenant_id, "role": p.role} for t, p in c.tokens.items() if t.startswith("demo-")]


# ------------------------------------------------------------------ documents
@router.post("/documents", status_code=202)
async def upload_document(request: Request, file: UploadFile = File(...), access_level: str = Form("employee"),
                          p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    if access_level not in RANK:
        raise HTTPException(422, f"access_level must be one of {ROLES}")
    if RANK[access_level] > p.rank:
        raise HTTPException(403, "cannot publish a document above your own role")
    limit = c.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"file larger than {c.settings.max_upload_mb} MB")
    try:
        res = await c.ingest.register_upload(p.tenant_id, file.filename or "upload", data, access_level, file.content_type, p.role)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if res.enqueue:
        await c.bus.publish(TOPIC_INGEST, str(res.document.id), {"document_id": str(res.document.id), "tenant_id": p.tenant_id})
    await audit(c, request, p, "document.upload", {"filename": res.document.filename, "action": res.action, "access_level": access_level})
    return {**_doc(res.document), "action": res.action}


@router.get("/documents")
async def list_documents(p: Principal = Depends(get_principal), c: Container = Depends(get_container)):
    async with c.session_factory() as s:
        rows = (await s.execute(select(Document).where(Document.tenant_id == p.tenant_id, Document.access_level <= p.rank).order_by(Document.created_at.desc()))).scalars().all()
    return [_doc(d) for d in rows]


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, p: Principal = Depends(get_principal), c: Container = Depends(get_container)):
    async with c.session_factory() as s:
        d = (await s.execute(select(Document).where(Document.id == document_id, Document.tenant_id == p.tenant_id, Document.access_level <= p.rank))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "document not found")
    return _doc(d)


@router.get("/documents/{document_id}/chunks")
async def document_chunks(document_id: uuid.UUID, limit: int = 200, p: Principal = Depends(get_principal), c: Container = Depends(get_container)):
    async with c.session_factory() as s:
        d = (await s.execute(select(Document).where(Document.id == document_id, Document.tenant_id == p.tenant_id, Document.access_level <= p.rank))).scalar_one_or_none()
        if d is None:
            raise HTTPException(404, "document not found")
        rows = (await s.execute(select(Chunk.id, Chunk.chunk_index, Chunk.page, Chunk.sheet, Chunk.section, Chunk.content).where(Chunk.document_id == d.id).order_by(Chunk.chunk_index).limit(min(limit, 500)))).all()
    return [{"id": str(r.id), "index": r.chunk_index, "page": r.page, "sheet": r.sheet, "section": r.section, "content": r.content} for r in rows]


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, request: Request, p: Principal = Depends(require_role("admin")), c: Container = Depends(get_container)):
    if not await c.ingest.delete_document(p.tenant_id, str(document_id)):
        raise HTTPException(404, "document not found")
    await audit(c, request, p, "document.delete", {"document_id": str(document_id)})


# ------------------------------------------------------------------ chat
@router.post("/chat")
async def chat(body: ChatRequest, request: Request, p: Principal = Depends(rate_limit), c: Container = Depends(get_container)):
    return await c.rag.answer(p, body.question, top_k=body.top_k, mode=body.mode, use_cache=body.use_cache, request_id=request.state.request_id)


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request, p: Principal = Depends(rate_limit), c: Container = Depends(get_container)):
    rid = request.state.request_id

    async def gen():
        try:
            async for ev in c.rag.stream(p, body.question, top_k=body.top_k, mode=body.mode, use_cache=body.use_cache, request_id=rid):
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface to the client without leaking internals
            yield f"event: error\ndata: {json.dumps({'message': type(exc).__name__})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": rid})


# ------------------------------------------------------------------ traces
@router.get("/traces/stats")
async def trace_stats(p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    return await c.traces.stats(p.tenant_id)


@router.get("/traces")
async def list_traces(limit: int = 50, p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    return await c.traces.list(p.tenant_id, min(limit, 200))


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: uuid.UUID, p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    t = await c.traces.get(p.tenant_id, str(trace_id))
    if t is None:
        raise HTTPException(404, "trace not found")
    return t


@router.post("/traces/{trace_id}/feedback")
async def trace_feedback(trace_id: uuid.UUID, body: FeedbackRequest, p: Principal = Depends(get_principal), c: Container = Depends(get_container)):
    if not await c.traces.set_feedback(p.tenant_id, str(trace_id), body.rating, body.comment):
        raise HTTPException(404, "trace not found")
    return {"ok": True}


# ------------------------------------------------------------------ eval
@router.post("/eval/run")
async def eval_run(request: Request, p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    for name, gen in [*((n, None) for n in CORPUS), *EXTRA_FILES.items()]:
        data = gen() if gen else read_demo(name)
        res = await c.ingest.register_upload(EVAL_TENANT, name, data, "admin", role="admin")
        if res.enqueue:
            await c.ingest.index_document(str(res.document.id))
    ep = Principal(EVAL_TENANT, "admin")

    async def search_fn(question: str, mode: str):
        return await c.rag.retrieve(ep, question, top_k=5, mode=mode)

    result = await run_eval(search_fn, QUESTIONS, k=5)
    await audit(c, request, p, "eval.run", {"recall_hybrid": result["hybrid_rrf"]["recall_at_5"], "mrr_hybrid": result["hybrid_rrf"]["mrr"]})
    return result


# ------------------------------------------------------------------ bitrix24
async def _accept_b24(c: Container, tenant_id: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    if not await c.cache.set_nx(f"idem:b24:{tenant_id}:{key}", 86400):
        return {"status": "duplicate", "idempotency_key": key}
    info = extract_event(payload)
    event_id = await c.tracker.create(tenant_id, key, info.event, payload)
    safe_payload = {k: v for k, v in payload.items() if k != "auth"}
    try:
        await c.bus.publish(TOPIC_BITRIX, key, {"event_id": event_id, "tenant_id": tenant_id, "payload": safe_payload})
    except Exception as exc:  # noqa: BLE001
        await c.cache.delete(f"idem:b24:{tenant_id}:{key}")  # let Bitrix retry
        await c.tracker.step(event_id, "publish_failed", status="failed", error=type(exc).__name__)
        raise HTTPException(503, "event bus unavailable") from exc
    await c.tracker.step(event_id, f"published:{TOPIC_BITRIX}", status="published")
    return {"status": "accepted", "event_id": event_id, "idempotency_key": key}


@router.post("/integrations/bitrix24/webhook", status_code=202)
async def bitrix_webhook(request: Request, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), c: Container = Depends(get_container)):
    if "json" in request.headers.get("content-type", ""):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object expected")
    else:
        payload = expand_bracket_keys({k: v for k, v in (await request.form()).items() if isinstance(v, str)})
    if not verify_application_token(payload, c.settings.b24_application_token):
        raise HTTPException(401, "invalid application_token")
    return await _accept_b24(c, c.settings.b24_tenant_id, payload, derive_idempotency_key(idempotency_key, payload))


@router.post("/integrations/bitrix24/simulate", status_code=202)
async def bitrix_simulate(body: SimulateLeadRequest, request: Request, p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    lead_id = body.lead_id or (uuid.uuid4().int % 90000 + 10000)
    payload = simulated_lead_payload(c.settings.b24_application_token, lead_id, body.title, body.comments)
    res = await _accept_b24(c, p.tenant_id, payload, f"sim-{uuid.uuid4().hex[:12]}")
    await audit(c, request, p, "bitrix24.simulate", {"lead_id": lead_id, "title": body.title})
    return res


@router.get("/integrations/events")
async def b24_events(p: Principal = Depends(require_role("manager")), c: Container = Depends(get_container)):
    return await c.tracker.list(p.tenant_id)
