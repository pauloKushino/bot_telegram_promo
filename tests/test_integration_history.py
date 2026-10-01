"""Teste de integração com PostgreSQL real (spec seção 10).

Precisa de um Postgres acessível. Defina TEST_DATABASE_URL, ex.:
    TEST_DATABASE_URL=postgresql+asyncpg://deals:deals_dev_password@localhost:15432/deals_test
Sem a variável, o módulo inteiro é pulado (não roda no CI sem banco).
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deals.db import repositories as repo
from deals.db.models import Base
from deals.engine.deals import PricePoint, evaluate_deal

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL não definida")

NOW = datetime.now(UTC)


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _insert_product_with_history(session: AsyncSession, points: list[tuple[str, int]]) -> int:
    product, _ = await repo.upsert_product(
        session,
        store="shopee",
        external_id="it-1",
        title="Mangá One Piece Vol. 105",
        url="https://shopee.com.br/p/1",
        image_url=None,
        seller_name="Loja",
        seller_rating=4.8,
        sales_count=500,
        commission_rate=None,
    )
    for preco, dias_atras in points:
        session.add(
            repo.PriceHistory(
                product_id=product.id,
                price=Decimal(preco),
                collected_at=NOW - timedelta(days=dias_atras),
            )
        )
    await session.commit()
    return product.id


async def test_upsert_e_historico_roundtrip(session: AsyncSession):
    product_id = await _insert_product_with_history(session, [("50.00", 30), ("45.00", 10), ("39.90", 1)])

    # upsert do mesmo produto não duplica
    product2, created = await repo.upsert_product(
        session,
        store="shopee",
        external_id="it-1",
        title="Mangá One Piece Vol. 105 (atualizado)",
        url="https://shopee.com.br/p/1",
        image_url=None,
        seller_name="Loja",
        seller_rating=4.9,
        sales_count=600,
        commission_rate=None,
    )
    await session.commit()
    assert not created
    assert product2.id == product_id

    history = await repo.get_price_history(session, product_id, since=NOW - timedelta(days=60))
    assert [h.price for h in history] == [Decimal("50.00"), Decimal("45.00"), Decimal("39.90")]
    # timezone preservado (timestamptz)
    assert history[0].collected_at.tzinfo is not None


async def test_queries_do_deal_engine_com_banco_real(session: AsyncSession):
    # queda real: menor que o mínimo anterior e >15% abaixo da mediana 30d
    product_id = await _insert_product_with_history(
        session, [("60.00", 40), ("60.00", 25), ("60.00", 10), ("44.90", 0)]
    )
    rows = await repo.get_price_history(session, product_id, since=NOW - timedelta(days=60))
    history = [PricePoint(price=r.price, collected_at=r.collected_at) for r in rows]

    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert verdict.is_deal
    assert "menor preço em 60 dias" in verdict.reason


async def test_price_history_nao_incha_com_preco_repetido(session: AsyncSession):
    product_id = await _insert_product_with_history(session, [("50.00", 0)])  # linha recente

    inseriu = await repo.maybe_insert_price(session, product_id, Decimal("50.00"))
    await session.commit()
    assert not inseriu  # mesmo preço, menos de 6h: não insere

    inseriu_novo = await repo.maybe_insert_price(session, product_id, Decimal("45.00"))
    await session.commit()
    assert inseriu_novo  # preço mudou: insere sempre


# -------------------------------------------------------------- fase 2


async def test_conversions_upsert_e_agregacao(session: AsyncSession):
    """Dedup por (store, order_id) e agregações do relatório diário (PostgreSQL real)."""
    start = NOW - timedelta(hours=1)
    end = NOW + timedelta(hours=1)

    nova = await repo.upsert_conversion(
        session, store="shopee", sub_id="p1xabcd123", order_id="ORD-1",
        commission=Decimal("3.50"), status="pending", occurred_at=NOW,
    )
    assert nova
    # reimportação do mesmo pedido com status novo: atualiza, não duplica
    atualizou = await repo.upsert_conversion(
        session, store="shopee", sub_id="p1xabcd123", order_id="ORD-1",
        commission=Decimal("3.50"), status="approved", occurred_at=NOW,
    )
    assert not atualizou
    await session.commit()

    convs = await repo.conversions_between(session, start, end)
    assert len(convs) == 1
    assert convs[0].status == "approved"
    assert await repo.commission_sum_between(session, start, end) == Decimal("3.50")

    # top de posts por comissão: amarra conversions.sub_id → post_queue
    product_id = await _insert_product_with_history(session, [("39.90", 0)])
    await repo.enqueue_post(
        session, product_id=product_id, price_at_post=Decimal("39.90"), reference_price=None,
        reason="teste", message_text="texto", affiliate_link="https://s.shopee.com.br/x",
        sub_id="p1xabcd123",
    )
    await session.commit()
    tops = await repo.top_posts_by_commission(session, start, end)
    assert len(tops) == 1
    assert tops[0][1] == Decimal("3.50")
