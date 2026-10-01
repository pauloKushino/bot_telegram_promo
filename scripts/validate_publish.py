"""Publica de verdade UM post pendente no canal (validação Fase 1).

Uso: uv run python scripts/validate_publish.py
"""

import asyncio

from sqlalchemy import select

from deals.db.base import AsyncSessionLocal
from deals.db.models import PostQueue
from deals.logging import setup_logging
from deals.worker.jobs import publish_job


async def main() -> None:
    setup_logging()
    await publish_job()
    async with AsyncSessionLocal() as s:
        posts = (await s.scalars(select(PostQueue).order_by(PostQueue.id.desc()).limit(3))).all()
    for p in posts:
        print(f"post #{p.id}: status={p.status} msg_id={p.telegram_message_id} posted_at={p.posted_at}")
        print(f"  link: {p.affiliate_link}")


if __name__ == "__main__":
    asyncio.run(main())
