"""Painel rápido de estado do sistema (fila, posts do dia, jobs).

Uso: uv run python scripts/check_status.py
"""

import asyncio
from datetime import UTC, datetime

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal
from deals.publisher.queue import local_day_start


async def main() -> None:
    now = datetime.now(UTC)
    day_start = local_day_start(now, settings.TIMEZONE)
    async with AsyncSessionLocal() as s:
        posted_hoje = await repo.count_posted_between(s, day_start, now)
        pending = await repo.count_pending_posts(s)
        ativos = await repo.count_active_products(s)
        novos_hoje = await repo.count_products_since(s, day_start)
        runs = await repo.get_job_runs(s)
        pausado = await repo.is_paused(s)
        last_posted = await repo.get_last_posted_at(s)

    print(f"agora UTC: {now:%Y-%m-%d %H:%M} | início do dia local: {day_start:%Y-%m-%d %H:%M} UTC")
    print(f"publicação pausada: {pausado}")
    print(f"posts publicados hoje: {posted_hoje} (máx/dia: {settings.MAX_POSTS_PER_DAY})")
    print(f"pendentes na fila: {pending}")
    print(f"produtos ativos: {ativos} | novos hoje: {novos_hoje}")
    print(f"último post: {last_posted}")
    print("último ciclo de cada job:")
    for nome, info in sorted(runs.items()):
        status = "ok" if info.get("ok") else "ERRO"
        print(f"  {nome}: {status} — {info.get('at')} — {info.get('detail')}")


if __name__ == "__main__":
    asyncio.run(main())
