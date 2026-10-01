"""Entrada do worker: `python -m deals.worker`.

Agenda os jobs do pipeline (coleta, classificação, fila de ofertas, publicação)
e os diários da fase 2 (importação de conversões + relatório ao admin) no mesmo
processo, sem infraestrutura extra (spec 3). Qualquer exceção dentro de um job
é capturada e logada — o worker não cai. Cada execução é registrada em
bot_settings (tabela chave-valor) para o comando /health do bot.
"""

import asyncio
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal
from deals.logging import setup_logging
from deals.worker.jobs import (
    build_queue_job,
    classify_job,
    collect_job,
    daily_report_job,
    import_conversions_job,
    publish_job,
)

CLASSIFY_INTERVAL_MIN = 5
BUILD_QUEUE_INTERVAL_MIN = 10
PUBLISH_INTERVAL_MIN = 1
IMPORT_CONVERSIONS_HOUR = 20  # 20:50
IMPORT_CONVERSIONS_MIN = 50
DAILY_REPORT_HOUR = 21  # 21:00, logo após a importação
DAILY_REPORT_MIN = 0


async def _record(name: str, ok: bool, detail: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await repo.record_job_run(session, name, ok, detail)
            await session.commit()
    except Exception:
        logger.exception("Falha ao registrar execução do job '{}'", name)


async def _safe(name: str, job: Callable[[], Awaitable[str | None]]) -> None:
    try:
        detail = await job()
        await _record(name, ok=True, detail=detail or "ok")
    except Exception as exc:
        logger.exception("Job '{}' falhou; seguindo para o próximo ciclo", name)
        await _record(name, ok=False, detail=f"{type(exc).__name__}: {exc}")


async def main() -> None:
    setup_logging()
    logger.info(
        "Worker iniciando (coleta a cada {} min, publish a cada {} min, modelo IA: {})",
        settings.COLLECT_INTERVAL_MIN, PUBLISH_INTERVAL_MIN, settings.AI_MODEL,
    )

    tz = ZoneInfo(settings.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(_safe, "interval", minutes=settings.COLLECT_INTERVAL_MIN,
                      args=["collect", collect_job], id="collect", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=CLASSIFY_INTERVAL_MIN,
                      args=["classify", classify_job], id="classify", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=BUILD_QUEUE_INTERVAL_MIN,
                      args=["build_queue", build_queue_job], id="build_queue", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "interval", minutes=PUBLISH_INTERVAL_MIN,
                      args=["publish", publish_job], id="publish", max_instances=1, coalesce=True)
    scheduler.add_job(_safe, "cron", hour=IMPORT_CONVERSIONS_HOUR, minute=IMPORT_CONVERSIONS_MIN,
                      args=["import_conversions", import_conversions_job], id="import_conversions")
    scheduler.add_job(_safe, "cron", hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MIN,
                      args=["daily_report", daily_report_job], id="daily_report")
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
