"""Validação manual do pipeline Fase 1 com APIs REAIS (Shopee + NVIDIA).

Executa SEQUENCIALMENTE: seed -> collect -> classify -> build_queue e imprime
um resumo de cada etapa. NÃO publica — para publicar, rode validate_publish.py.

Uso: uv run python scripts/validate_pipeline.py
"""

import asyncio

from sqlalchemy import func, select

from deals.db.base import AsyncSessionLocal
from deals.db.models import PostQueue, PostStatus, PriceHistory, Product
from deals.logging import setup_logging
from deals.worker.jobs import build_queue_job, classify_job, collect_job


async def main() -> None:
    setup_logging()

    print("\n===== 1) COLLECT (Shopee real) =====")
    await collect_job()
    async with AsyncSessionLocal() as s:
        n_products = await s.scalar(select(func.count(Product.id)))
        n_prices = await s.scalar(select(func.count(PriceHistory.id)))
    print(f"-> produtos no banco: {n_products} | linhas de price_history: {n_prices}")

    print("\n===== 2) CLASSIFY (IA real, até 50 produtos) =====")
    await classify_job()
    async with AsyncSessionLocal() as s:
        classified = (
            await s.scalars(
                select(Product).where(Product.is_anime_merch.is_not(None)).limit(25)
            )
        ).all()
    for p in classified:
        flag = "ANIME" if p.is_anime_merch else "---- "
        print(
            f"  [{flag}] conf={p.official_confidence} tipo={p.product_type} "
            f"franquia={p.franchise!r} | {p.title[:60]}"
        )

    print("\n===== 3) BUILD QUEUE (deal engine + copywriter reais) =====")
    await build_queue_job()
    async with AsyncSessionLocal() as s:
        pending = (
            await s.scalars(
                select(PostQueue).where(PostQueue.status == PostStatus.PENDING).limit(5)
            )
        ).all()
    print(f"-> posts pendentes na fila: {len(pending)}")
    for post in pending:
        print(f"\n--- post #{post.id} | produto {post.product_id} | R$ {post.price_at_post} | {post.sub_id}")
        print(f"    reason: {post.reason}")
        print(f"    link: {post.affiliate_link}")
        print(f"    legenda:\n{post.message_text}")


if __name__ == "__main__":
    asyncio.run(main())
