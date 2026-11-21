#!/usr/bin/env python3
"""Guided demo of every endpoint:  python scripts/demo.py [--base-url http://localhost:8000]"""
from __future__ import annotations

import argparse
import json

import _http as h


def show(title: str, value) -> None:
    print(f"\n=== {title}")
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)[:900])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    h.BASE = ap.parse_args().base_url.rstrip("/")
    h.wait_until(lambda: h.request("GET", "/ready")[0] == 200, 120, 1.0, "/ready")
    admin, manager, employee, guest = (h.tok("acme", r) for r in ("admin", "manager", "employee", "guest"))

    show("1. Кто я (роль и тенант берутся из токена)", h.request("GET", "/api/v1/me", token=guest)[1])
    show("2. RBAC: документы, видимые guest vs admin", {
        "guest": [d["filename"] for d in h.request("GET", "/api/v1/documents", token=guest)[1]],
        "admin": [d["filename"] for d in h.request("GET", "/api/v1/documents", token=admin)[1]],
    })
    q = "Какой оклад у грейда Senior?"
    for role, t in (("guest", guest), ("admin", admin)):
        r = h.request("POST", "/api/v1/chat", token=t, body={"question": q})[1]
        show(f"3. Вопрос «{q}» от роли {role}", {"answer": r["answer"], "refused": r["refused"], "sources": [c["filename"] for c in r["citations"]]})
    r = h.request("POST", "/api/v1/chat", token=employee, body={"question": "Остаток по артикулу CB-77120"})[1]
    show("4. Точный поиск по артикулу (FTS + векторы + RRF)", {"answer": r["answer"], "sources": [(c["filename"], c["location"], c["score"]) for c in r["citations"]]})
    r2 = h.request("POST", "/api/v1/chat", token=employee, body={"question": "Остаток по артикулу CB-77120"})[1]
    show("5. Повтор того же вопроса", {"cache_hit": r2["cache_hit"]})
    answer, cites, done = h.stream_chat(manager, "Какая неустойка по договору ДГ-2024/17?")
    show("6. SSE-стриминг", {"answer": answer, "done": done})
    h.request("POST", f"/api/v1/traces/{done['trace_id']}/feedback", token=manager, body={"rating": 1, "comment": "точно"})
    show("7. Трейс + фидбэк", h.request("GET", f"/api/v1/traces/{done['trace_id']}", token=manager)[1])
    show("8. KPI", h.request("GET", "/api/v1/traces/stats", token=manager)[1])
    h.request("POST", "/api/v1/integrations/bitrix24/simulate", token=manager, body={})
    ev = h.wait_until(lambda: next((e for e in h.request("GET", "/api/v1/integrations/events", token=manager)[1] if e["status"] in ("done", "dlq")), None), 60, 0.5, "bitrix event")
    show("9. Лид Битрикс24 -> Kafka -> воркер -> черновик комментария", {"steps": [s["step"] for s in ev["steps"]], "comment": ev["result"].get("comment")})
    e = h.request("POST", "/api/v1/eval/run", token=manager)[1]
    show("10. Eval", {m: {"recall_at_5": e[m]["recall_at_5"], "mrr": e[m]["mrr"], "avg_latency_ms": e[m]["avg_latency_ms"]} for m in ("vector_only", "hybrid_rrf")})
    print("\nГотово. Откройте UI: " + h.BASE + "/")


if __name__ == "__main__":
    main()
