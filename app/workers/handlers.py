from __future__ import annotations

import logging
from typing import Any

from app.core.resilience import PermanentError
from app.events.bus import TOPIC_BITRIX, TOPIC_INGEST, process_with_retries
from app.integrations.bitrix24 import extract_event, format_draft_comment
from app.security.auth import Principal

log = logging.getLogger("rag.worker")


class Handlers:
    def __init__(self, c):
        self.c = c

    # --- documents.ingest -------------------------------------------------
    async def ingest(self, msg: dict[str, Any]) -> None:
        outcome = await self.c.ingest.index_document(msg["document_id"])
        log.info("ingest %s -> %s", msg["document_id"], outcome)

    async def ingest_failed(self, msg: dict[str, Any], exc: Exception) -> None:
        await self.c.ingest.mark_failed(msg["document_id"], f"{type(exc).__name__}: {exc}")

    # --- bitrix24.events --------------------------------------------------
    async def bitrix(self, msg: dict[str, Any]) -> None:
        c, ev_id = self.c, msg["event_id"]
        await c.tracker.step(ev_id, "consumed:rag.bitrix24.events", status="processing")
        info = extract_event(msg["payload"])
        if not info.entity_id:
            raise PermanentError("event has no entity id")
        title, comments = info.title, info.comments
        if not title:
            entity = await c.bitrix.fetch_entity(info.entity_type, info.entity_id) or {}
            title, comments = str(entity.get("TITLE") or ""), str(entity.get("COMMENTS") or "")
            await c.tracker.step(ev_id, "b24:fetch_entity", found=bool(entity))
        query = " ".join(x for x in (title, comments) if x).strip()
        if not query:
            raise PermanentError("nothing to search for")
        result = await c.rag.answer(Principal(msg["tenant_id"], "employee"), query, use_cache=False, request_id=f"b24-{ev_id}")
        await c.tracker.step(ev_id, "kb_search", sources=len(result.get("citations", [])), refused=result["refused"])
        comment = format_draft_comment(result["answer"], result.get("citations", []), result["refused"])
        posted = await c.bitrix.add_timeline_comment(info.entity_type, info.entity_id, comment)
        mode = "dry-run" if posted.get("dry_run") else "live"
        await c.tracker.step(ev_id, f"b24:crm.timeline.comment.add ({mode})", status="done", result={"comment": comment, "mode": mode, "entity": f"{info.entity_type}:{info.entity_id}"})

    async def bitrix_failed(self, msg: dict[str, Any], exc: Exception) -> None:
        await self.c.tracker.step(msg["event_id"], "dead_letter:rag.dlq", status="dlq", error=f"{type(exc).__name__}: {exc}"[:300])


async def start_consumers(c) -> list:
    h, s = Handlers(c), c.settings

    async def on_ingest(msg):
        await process_with_retries(c.bus, TOPIC_INGEST, msg, h.ingest, max_attempts=s.max_retries, base_delay=s.retry_base_delay, on_final_failure=h.ingest_failed)

    async def on_bitrix(msg):
        await process_with_retries(c.bus, TOPIC_BITRIX, msg, h.bitrix, max_attempts=s.max_retries, base_delay=s.retry_base_delay, on_final_failure=h.bitrix_failed)

    tasks = [await c.bus.subscribe([TOPIC_INGEST], "rag-ingest", on_ingest), await c.bus.subscribe([TOPIC_BITRIX], "rag-bitrix", on_bitrix)]
    c.consumers.extend(tasks)
    return tasks
