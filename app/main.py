from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app.api.routes import router
from app.config import Settings, get_settings
from app.container import build_container
from app.seed.demo import seed_demo
from app.workers.handlers import start_consumers

log = logging.getLogger("rag.api")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def create_app(settings: Settings | None = None, container=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        c = container or build_container(settings)
        app.state.c = c
        await c.bus.start()
        if settings.event_bus == "memory":  # broker-less mode: consume in-process; with Kafka the `worker` service does it
            await start_consumers(c)
        if settings.seed_demo:
            try:
                log.info("demo seed: %s", await seed_demo(c) or "nothing to do")
            except Exception:  # noqa: BLE001 - the API must still boot
                log.exception("demo seed failed")
        yield
        await c.close()

    app = FastAPI(title="rag-knowledge-hub", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready(request: Request):
        c = request.app.state.c
        try:
            async with c.session_factory() as s:
                await s.execute(text("SELECT 1"))
            await c.cache.ping()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"status": "not_ready", "error": type(exc).__name__}, status_code=503)
        return {"status": "ready"}

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND)

    app.include_router(router)
    return app


app = create_app()
