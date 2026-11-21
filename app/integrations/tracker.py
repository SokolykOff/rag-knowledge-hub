"""Persists the path of a Bitrix24 event through the system (received -> Kafka -> worker -> comment)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import IntegrationEvent


def event_to_dict(e: IntegrationEvent) -> dict[str, Any]:
    return {
        "id": str(e.id), "tenant_id": e.tenant_id, "idempotency_key": e.idempotency_key, "event": e.event_name, "status": e.status,
        "steps": e.steps or [], "result": e.result or {}, "created_at": e.created_at.isoformat() if e.created_at else None,
    }


class EventTracker:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def create(self, tenant_id: str, key: str, event_name: str, payload: dict[str, Any]) -> str:
        safe = {k: v for k, v in payload.items() if k != "auth"}  # never persist the application token
        async with self._sf() as s, s.begin():
            row = IntegrationEvent(id=uuid.uuid4(), tenant_id=tenant_id, idempotency_key=key, event_name=event_name, status="received", payload=safe,
                                   steps=[{"step": "webhook_received", "ts": datetime.now(timezone.utc).isoformat()}])
            s.add(row)
            return str(row.id)

    async def step(self, event_id: str, step: str, *, status: str | None = None, result: dict | None = None, **detail: Any) -> None:
        async with self._sf() as s, s.begin():
            row = (await s.execute(select(IntegrationEvent).where(IntegrationEvent.id == uuid.UUID(event_id)).with_for_update())).scalar_one_or_none()
            if row is None:
                return
            row.steps = [*(row.steps or []), {"step": step, "ts": datetime.now(timezone.utc).isoformat(), **detail}]
            if status:
                row.status = status
            if result is not None:
                row.result = result

    async def list(self, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self._sf() as s:
            rows = (await s.execute(select(IntegrationEvent).where(IntegrationEvent.tenant_id == tenant_id).order_by(IntegrationEvent.created_at.desc()).limit(limit))).scalars().all()
        return [event_to_dict(r) for r in rows]
