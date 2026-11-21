"""Standalone Kafka consumer process: `python -m app.workers.worker`."""
from __future__ import annotations

import asyncio
import logging
import signal

from app.config import get_settings
from app.container import build_container
from app.workers.handlers import start_consumers

log = logging.getLogger("rag.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    c = build_container(settings)
    await c.bus.start()
    tasks = await start_consumers(c)
    log.info("worker started (bus=%s)", settings.event_bus)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    done, _ = await asyncio.wait([asyncio.create_task(stop.wait()), *tasks], return_when=asyncio.FIRST_COMPLETED)
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
