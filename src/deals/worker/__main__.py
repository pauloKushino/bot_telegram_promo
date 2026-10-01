"""Entrada do worker: `python -m deals.worker`.

Agenda os jobs do pipeline (coleta, classificação, fila de ofertas e
publicação) no mesmo processo, sem infraestrutura extra (spec 3).
Qualquer exceção dentro de um job é capturada e logada — o worker não cai.
"""

import asyncio
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from deals.config import settings
from deals.logging import setup_logging
from deals.worker.jobs import build_queue_job, classify_job, collect_job, publish_job

CLASSIFY_INTERVAL_MIN = 5
BUILD_QUEUE_INTERVAL_MIN = 10
PUBLISH_INTERVAL_MIN = 1


async def _safe(name: str, job: Callable[[], Awaitable[None]]) -> None:
    try:
        await job()
    except Exception:
        logger.exception("Job '{}' falhou; seguindo para o próximo ciclo", name)


async def main() -> None:
    setup_logging()
    logger.info(
        "Worker iniciando (coleta a cada {} min, publish a cada {} min, modelo IA: {})",
        settings.COLLECT_INTERVAL_MIN, PUBLISH_INTERVAL_MIN, settings.AI_MODEL,
    )

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.TIMEZONE))
    scheduler.add_job(_safe, "interval", minutes=settings.COLLECT_INTERVAL_MIN,
                      args=["collect", collect_job], id="collect", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=CLASSIFY_INTERVAL_MIN,
                      args=["classify", classify_job], id="classify", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=BUILD_QUEUE_INTERVAL_MIN,
                      args=["build_queue", build_queue_job], id="build_queue", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=PUBLISH_INTERVAL_MIN,
                      args=["publish", publish_job], id="publish", max_instances=1, coalesce=True)
    scheduler.start()

    # Primeiro ciclo imediato: não esperar o primeiro intervalo
    await _safe("collect", collect_job)
    await _safe("classify", classify_job)
    await _safe("build_queue", build_queue_job)

    try:
        await asyncio.Event().wait()  # roda para sempre; jobs são do scheduler
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
