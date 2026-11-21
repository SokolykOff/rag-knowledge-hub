"""Bitrix24: inbound webhook helpers + outbound REST client (timeouts, retries, 429 Retry-After, circuit breaker, dry-run)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.resilience import CircuitBreaker, TransientError, request_with_retries

ENTITY_BY_EVENT = {
    "ONCRMLEADADD": "lead", "ONCRMLEADUPDATE": "lead", "ONCRMDEALADD": "deal", "ONCRMDEALUPDATE": "deal",
}
_KEY = re.compile(r"\[([^\]]*)\]")


def expand_bracket_keys(flat: dict[str, Any]) -> dict[str, Any]:
    """`data[FIELDS][ID]=5` (Bitrix form encoding) -> {'data': {'FIELDS': {'ID': '5'}}}."""
    out: dict[str, Any] = {}
    for key, value in flat.items():
        head = key.split("[", 1)[0]
        path = [head] + _KEY.findall(key)
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return out


def application_token(payload: dict[str, Any]) -> str:
    return str(((payload.get("auth") or {}).get("application_token")) or "")


def verify_application_token(payload: dict[str, Any], expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(application_token(payload).encode(), expected.encode())


def derive_idempotency_key(header_value: str | None, payload: dict[str, Any]) -> str:
    if header_value:
        return header_value[:128]
    body = {k: v for k, v in payload.items() if k != "auth"}  # the token must not influence/leak via the key
    return "auto-" + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


@dataclass
class B24Event:
    event: str
    entity_type: str
    entity_id: str
    title: str
    comments: str


def extract_event(payload: dict[str, Any]) -> B24Event:
    event = str(payload.get("event") or "").upper()
    fields = ((payload.get("data") or {}).get("FIELDS")) or {}
    return B24Event(
        event=event,
        entity_type=ENTITY_BY_EVENT.get(event, "lead"),
        entity_id=str(fields.get("ID") or ""),
        title=str(fields.get("TITLE") or ""),
        comments=str(fields.get("COMMENTS") or fields.get("SOURCE_DESCRIPTION") or ""),
    )


def simulated_lead_payload(token: str, lead_id: int, title: str, comments: str) -> dict[str, Any]:
    return {
        "event": "ONCRMLEADADD", "event_handler_id": "1", "ts": "0",
        "data": {"FIELDS": {"ID": str(lead_id), "TITLE": title, "COMMENTS": comments}},
        "auth": {"application_token": token, "domain": "demo.bitrix24.ru"},
    }


class Bitrix24Client:
    def __init__(self, webhook_url: str = "", *, dry_run: bool = True, timeout: float = 10.0, max_retries: int = 3,
                 base_delay: float = 0.5, breaker: CircuitBreaker | None = None, transport=None, sleep=None):
        self.webhook_url = webhook_url.rstrip("/") + "/" if webhook_url else ""
        self.dry_run = dry_run or not webhook_url
        self.timeout, self.max_retries, self.base_delay = timeout, max_retries, base_delay
        self.breaker = breaker or CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        self._transport = transport
        self._sleep = sleep

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run:
            return {"result": 0, "dry_run": True, "method": method, "params": params}
        import httpx

        kwargs = {"sleep": self._sleep} if self._sleep else {}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:

            async def send():
                try:
                    r = await client.post(f"{self.webhook_url}{method}.json", json=params)
                except httpx.TransportError as exc:  # includes timeouts
                    raise TransientError(str(exc)) from exc
                try:
                    body = r.json()
                except ValueError:
                    body = {"raw": r.text[:200]}
                return r.status_code, dict(r.headers), body

            return await request_with_retries(send, max_retries=self.max_retries, base_delay=self.base_delay, breaker=self.breaker, **kwargs)

    async def fetch_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        if self.dry_run or not entity_id:
            return None
        body = await self.call(f"crm.{entity_type}.get", {"id": entity_id})
        return body.get("result")

    async def add_timeline_comment(self, entity_type: str, entity_id: str, comment: str) -> dict[str, Any]:
        return await self.call("crm.timeline.comment.add", {"fields": {"ENTITY_ID": entity_id, "ENTITY_TYPE": entity_type, "COMMENT": comment}})


def format_draft_comment(answer: str, citations: list[dict[str, Any]], refused: bool) -> str:
    if refused:
        return "[AI-черновик] По базе знаний релевантной информации не найдено. Требуется ручная обработка."
    sources = "; ".join(sorted({f"{c['filename']} ({c['location']})" if c.get("location") else c["filename"] for c in citations}))
    return f"[AI-черновик, проверьте перед отправкой]\n{answer}\n\nИсточники: {sources}"
