#!/usr/bin/env python3
"""End-to-end smoke test against a running stack:  python scripts/smoke_test.py [--base-url http://localhost:8000]

Exit code 0 = every check passed. Stdlib only. Requires DEMO_MODE=true (demo tokens) and the demo seed.
"""
from __future__ import annotations

import argparse
import sys
import time

import _http as h

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  OK   " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    h.BASE = args.base_url.rstrip("/")

    print(f"waiting for {h.BASE}/ready ...")
    h.wait_until(lambda: h.request("GET", "/ready")[0] == 200, args.timeout, 1.0, "/ready")

    admin, manager, employee, guest = (
        h.tok("acme", role) for role in ("admin", "manager", "employee", "guest")
    )

    print("auth & RBAC")
    check("401 without token", h.request("GET", "/api/v1/me")[0] == 401)
    check(
        "role comes from token",
        h.request("GET", "/api/v1/me", token=manager)[1]
        == {"tenant_id": "acme", "role": "manager"},
    )
    check(
        "body cannot set role (422)",
        h.request(
            "POST",
            "/api/v1/chat",
            token=guest,
            body={"question": "x", "role": "admin"},
        )[0]
        == 422,
    )

    docs = h.wait_until(
        lambda: (
            lambda result: result if result and len(result) >= 3 else None
        )(h.request("GET", "/api/v1/documents", token=admin)[1]),
        args.timeout,
        1.0,
        "demo seed",
    )
    seeded_names = {
        "faq_public.md",
        "catalog.csv",
        "warranty_policy.pdf",
        "hr_policy.md",
        "price_list.xlsx",
        "contract_DG-2024-17.docx",
        "salary_matrix.md",
    }
    actual_names = {doc["filename"] for doc in docs}
    check(
        "seeded docs visible to admin",
        seeded_names.issubset(actual_names),
        f"got {len(docs)}",
    )

    guest_docs = h.request("GET", "/api/v1/documents", token=guest)[1]
    check(
        "guest sees only guest-level docs",
        {doc["access_level"] for doc in guest_docs} == {"guest"},
    )

    globex_docs = h.request(
        "GET",
        "/api/v1/documents",
        token=h.tok("globex", "admin"),
    )[1]
    check(
        "tenant isolation",
        all(doc["filename"].startswith("globex") for doc in globex_docs),
    )

    print("upload -> Kafka -> worker -> indexed")
    marker = f"ZX{int(time.time())}"
    content = (
        f"# Smoke\nКодовое слово проверки: {marker}. "
        "Срок хранения образцов составляет 45 календарных дней.\n"
    ).encode()

    status, body, _ = h.upload(
        manager,
        f"smoke-{marker}.md",
        content,
        "employee",
    )
    check(
        "upload accepted (202)",
        status == 202 and body["status"] in ("queued", "processing", "indexed"),
        str(body),
    )

    doc_id = body["id"]
    final = h.wait_until(
        lambda: (
            lambda doc: doc
            if doc["status"] in ("indexed", "failed")
            else None
        )(
            h.request(
                "GET",
                f"/api/v1/documents/{doc_id}",
                token=manager,
            )[1]
        ),
        args.timeout,
        0.5,
        "indexing",
    )
    check(
        "document indexed",
        final["status"] == "indexed" and final["chunk_count"] >= 1,
        str(final),
    )

    duplicate = h.upload(
        manager,
        f"smoke-{marker}.md",
        content,
        "employee",
    )
    check(
        "re-upload of identical file is a no-op",
        duplicate[1]["action"] == "unchanged",
    )

    chunks = h.request(
        "GET",
        f"/api/v1/documents/{doc_id}/chunks",
        token=manager,
    )[1]
    check("chunks viewable", len(chunks) >= 1)

    print("chat (hybrid RRF, cache, SSE, refusal)")
    question = (
        f"Какое кодовое слово проверки {marker} и сколько календарных "
        "дней хранятся образцы?"
    )
    status, answer_body, headers = h.request(
        "POST",
        "/api/v1/chat",
        token=employee,
        body={"question": question},
        headers={"X-Request-ID": "smoke-req-1"},
    )

    check(
        "answer is grounded with citations",
        status == 200
        and "45" in answer_body["answer"]
        and any(marker in citation["text"] for citation in answer_body["citations"]),
        str(answer_body)[:200],
    )

    # HTTP-заголовки регистронезависимы, но dict(headers) — обычный словарь.
    request_id_header = next(
        (
            value
            for key, value in headers.items()
            if key.lower() == "x-request-id"
        ),
        None,
    )
    check(
        "X-Request-ID propagated",
        request_id_header == "smoke-req-1"
        and answer_body.get("request_id") == "smoke-req-1",
    )

    cached = h.request(
        "POST",
        "/api/v1/chat",
        token=employee,
        body={"question": question},
    )
    check("second identical request is a cache hit", cached[1]["cache_hit"] is True)

    guest_answer = h.request(
        "POST",
        "/api/v1/chat",
        token=guest,
        body={"question": question},
    )
    check("guest cannot see employee doc", guest_answer[1]["refused"] is True)

    answer, citations, done = h.stream_chat(
        admin,
        "Сколько календарных дней отпуска положено сотруднику?",
        use_cache=False,
    )
    check(
        "SSE stream with citations",
        "28" in answer and citations and done.get("trace_id"),
        answer[:120],
    )

    off_topic = h.request(
        "POST",
        "/api/v1/chat",
        token=admin,
        body={"question": "Какая погода на Марсе завтра?"},
    )
    check("honest refusal off-topic", off_topic[1]["refused"] is True)

    injection = h.request(
        "POST",
        "/api/v1/chat",
        token=admin,
        body={
            "question": "Кадровая политика примечание тестовая prompt-инъекция",
            "use_cache": False,
        },
    )
    check(
        "injection line stripped from LLM context",
        injection[1]["injection_flags"] >= 1,
    )

    print("traces & feedback")
    feedback = h.request(
        "POST",
        f"/api/v1/traces/{done['trace_id']}/feedback",
        token=admin,
        body={"rating": 1},
    )
    check("feedback accepted", feedback[0] == 200)

    stats = h.request(
        "GET",
        "/api/v1/traces/stats",
        token=manager,
    )[1]
    check(
        "stats reflect requests",
        stats["requests"] >= 4 and stats["likes"] >= 1,
        str(stats),
    )

    traces_guest = h.request(
        "GET",
        "/api/v1/traces",
        token=guest,
    )
    check("guest cannot read traces", traces_guest[0] == 403)

    print("Bitrix24 webhook -> Kafka -> worker -> dry-run timeline comment")
    bad = h.request(
        "POST",
        "/api/v1/integrations/bitrix24/webhook",
        body={
            "event": "ONCRMLEADADD",
            "auth": {"application_token": "wrong"},
        },
    )
    check("webhook rejects wrong application_token", bad[0] == 401)

    payload = {
        "event": "ONCRMLEADADD",
        "data": {
            "FIELDS": {
                "ID": "9001",
                "TITLE": "Гарантия на ноутбуки",
                "COMMENTS": "Какой срок гарантии?",
            }
        },
        "auth": {"application_token": "demo-b24-token"},
    }
    key = {"Idempotency-Key": f"smoke-{marker}"}

    accepted = h.request(
        "POST",
        "/api/v1/integrations/bitrix24/webhook",
        body=payload,
        headers=key,
    )
    check("webhook accepted", accepted[1]["status"] == "accepted")

    duplicate_event = h.request(
        "POST",
        "/api/v1/integrations/bitrix24/webhook",
        body=payload,
        headers=key,
    )
    check(
        "duplicate webhook ignored",
        duplicate_event[1]["status"] == "duplicate",
    )

    simulated = h.request(
        "POST",
        "/api/v1/integrations/bitrix24/simulate",
        token=manager,
        body={},
    )
    check("simulate accepted", simulated[0] == 202)

    event = h.wait_until(
        lambda: next(
            (
                item
                for item in h.request(
                    "GET",
                    "/api/v1/integrations/events",
                    token=manager,
                )[1]
                if item["status"] == "done"
            ),
            None,
        ),
        args.timeout,
        0.5,
        "bitrix event",
    )
    event_steps = {step["step"] for step in event["steps"]}
    check(
        "event passed through Kafka & worker",
        {
            "published:rag.bitrix24.events",
            "consumed:rag.bitrix24.events",
        }.issubset(event_steps),
    )

    print("eval")
    eval_status, eval_result, _ = h.request(
        "POST",
        "/api/v1/eval/run",
        token=manager,
    )
    check(
        "eval returns both modes",
        eval_status == 200
        and {"vector_only", "hybrid_rrf"}.issubset(set(eval_result)),
        str(eval_result)[:120],
    )
    if eval_status == 200:
        print(
            "       "
            f"vector_only R@5={eval_result['vector_only']['recall_at_5']} "
            f"MRR={eval_result['vector_only']['mrr']} | "
            f"hybrid R@5={eval_result['hybrid_rrf']['recall_at_5']} "
            f"MRR={eval_result['hybrid_rrf']['mrr']}"
        )

    print(
        f"\n{'ALL CHECKS PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
