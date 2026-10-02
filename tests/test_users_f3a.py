"""Testes da fase 3a (usuários, follows, alertas, DMs) com PostgreSQL real.

Precisa de TEST_DATABASE_URL (ver tests/test_integration_history.py).
Pulados quando a variável não existe.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deals.db import repositories as repo
from deals.db.models import Base, PostQueue, PostStatus
from deals.publisher.dm import DmSkip, check_can_dm, try_send_dm
from deals.worker.jobs import check_alerts_job
from tests.fakes import FakeBot, FakeStoreAdapter

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL não definida")

NOW = datetime.now(UTC)
FREE_FOLLOWS = 3
FREE_ALERTS = 1


@pytest.fixture
async def engine():
    e = create_async_engine(TEST_URL)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield e
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await e.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
def session_factory(engine, monkeypatch):
    """Jobs abrem a própria sessão: redireciona para o banco de teste."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("deals.worker.jobs.AsyncSessionLocal", factory)
    return factory


async def _user(session: AsyncSession, tg_id: int = 1001) -> int:
    user = await repo.get_or_create_user(session, tg_id, "tester", "Test User")
    await session.commit()
    return user.id


async def _product(session: AsyncSession, franchise: str = "One Piece", external_id: str = "p1") -> int:
    product, _ = await repo.upsert_product(
        session, store="shopee", external_id=external_id, title="Mangá One Piece Vol. 105",
        url="https://shopee.com.br/p/1", image_url=None, seller_name="Loja", seller_rating=4.8,
        sales_count=100, commission_rate=None,
    )
    await repo.apply_classification(
        session, product.id, is_anime_merch=True, franchise=franchise, product_type="manga",
        volume="105", publisher="Panini", official_confidence=0.95,
    )
    await session.commit()
    return product.id


# ------------------------------------------------------------------ users/follows


async def test_start_registra_e_reativa(session: AsyncSession):
    uid = await _user(session, 1001)
    await repo.deactivate_user(session, 1001)  # simula /parar
    await session.commit()
    # voltou: /start reativa
    user = await repo.get_or_create_user(session, 1001, "tester", "Test User")
    await session.commit()
    assert user.id == uid and user.is_active


async def test_follow_limite_free_toggle(session: AsyncSession):
    uid = await _user(session)
    for fr in ["one piece", "naruto", "jujutsu kaisen"]:
        segue, _ = await repo.toggle_follow(session, uid, fr, FREE_FOLLOWS)
        assert segue
    assert await repo.count_active_follows(session, uid) == 3

    # 4ª franquia: bloqueada pelo limite free
    segue, msg = await repo.toggle_follow(session, uid, "bleach", FREE_FOLLOWS)
    assert not segue
    assert "Limite" in msg

    # toggle off libera slot
    segue, _ = await repo.toggle_follow(session, uid, "naruto", FREE_FOLLOWS)
    assert not segue
    segue, _ = await repo.toggle_follow(session, uid, "bleach", FREE_FOLLOWS)
    assert segue
    # idempotente: unicidade (user, franchise)
    follows = await repo.get_user_follows(session, uid)
    assert sorted(f.franchise for f in follows) == ["bleach", "jujutsu kaisen", "one piece"]


async def test_seguidores_da_franquia_respeitam_flags(session: AsyncSession):
    uid_a = await _user(session, 2001)
    uid_b = await _user(session, 2002)  # vai "parar"
    uid_c = await _user(session, 2003)  # vai bloquear o bot
    for uid in (uid_a, uid_b, uid_c):
        await repo.toggle_follow(session, uid, "one piece", FREE_FOLLOWS)
    await repo.deactivate_user(session, 2002)
    await repo.mark_user_blocked(session, 2003)
    await session.commit()

    followers = await repo.get_followers_of_franchise(session, "One Piece")
    assert [u.tg_id for u in followers] == [2001]


# ------------------------------------------------------------------ alertas


async def test_alertas_limite_update_e_trigger(session: AsyncSession):
    uid = await _user(session)
    p1 = await _product(session, external_id="p1")
    p2 = await _product(session, external_id="p2")

    alert, status = await repo.upsert_price_alert(session, uid, p1, Decimal("39.90"), FREE_ALERTS)
    assert status == "created"
    # mesmo produto: atualiza o alvo, não consome slot
    alert2, status = await repo.upsert_price_alert(session, uid, p1, Decimal("34.00"), FREE_ALERTS)
    assert status == "updated" and alert2.id == alert.id
    assert alert2.target_price == Decimal("34.00")
    # segundo produto: estoura o limite free
    alert_none, status = await repo.upsert_price_alert(session, uid, p2, Decimal("50.00"), FREE_ALERTS)
    assert alert_none is None and status == "limit"

    # lista de alertas ativos para o job
    rows = await repo.get_active_alerts_with_products(session)
    assert len(rows) == 1 and rows[0][0].target_price == Decimal("34.00")

    await repo.mark_alert_triggered(session, alert)
    await session.commit()
    assert await repo.get_active_alerts_with_products(session) == []
    # após disparar, o slot libera de novo
    alert3, status = await repo.upsert_price_alert(session, uid, p2, Decimal("50.00"), FREE_ALERTS)
    assert status == "created"


async def test_job_dispara_alerta_quando_preco_cai(session: AsyncSession, session_factory):
    uid = await _user(session, 3001)
    product_id = await _product(session)
    await repo.upsert_price_alert(session, uid, product_id, Decimal("40.00"), FREE_ALERTS)

    # histórico: 45.00 → 39.90 (abaixo do alvo 40.00)
    h2, h1 = NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    session.add_all([
        repo.PriceHistory(product_id=product_id, price=Decimal("45.00"), collected_at=h2),
        repo.PriceHistory(product_id=product_id, price=Decimal("39.90"), collected_at=h1),
    ])
    # post anterior p/ permalink do botão? não precisa — o alerta usa o link do produto
    await session.commit()

    bot = FakeBot()
    resultado = await check_alerts_job(bot=bot, adapters=[FakeStoreAdapter()])

    assert resultado and "1 alertas disparados" in resultado
    assert len(bot.sent) == 1
    dm = bot.sent[0]
    assert dm["chat_id"] == 3001
    assert "R$ 39,90" in dm["text"]
    assert "R$ 40,00" in dm["text"]  # alvo
    assert "Link de afiliado" in dm["text"]  # disclosure de afiliado na DM também
    # alerta one-shot: sumiu dos ativos
    assert await repo.get_active_alerts_with_products(session) == []


async def test_job_nao_dispara_quando_preco_acima_do_alvo(session: AsyncSession, session_factory):
    uid = await _user(session, 3002)
    product_id = await _product(session)
    await repo.upsert_price_alert(session, uid, product_id, Decimal("30.00"), FREE_ALERTS)
    session.add(repo.PriceHistory(product_id=product_id, price=Decimal("45.00"), collected_at=NOW))
    await session.commit()

    bot = FakeBot()
    resultado = await check_alerts_job(bot=bot, adapters=[FakeStoreAdapter()])
    assert resultado and "0 alertas disparados" in resultado
    assert bot.sent == []
    assert len(await repo.get_active_alerts_with_products(session)) == 1


# ------------------------------------------------------------------ DM rules


async def test_dm_limite_diario_e_bloqueio(session: AsyncSession):
    await _user(session, 4001)
    user = await repo.get_user_by_tg(session, 4001)

    bot = FakeBot()
    from deals.config import settings

    for _ in range(settings.MAX_DM_PER_USER_DAY):
        assert await try_send_dm(bot, session, user, "oi", kind="teste")
        await session.commit()
    # estourou o limite
    assert not await try_send_dm(bot, session, user, "extra", kind="teste")

    # usuário bloqueou o bot: marcado e não recebe mais
    await session.execute(repo.sql_delete(repo.DmLog))  # limpa o log p/ isolar
    await session.commit()
    blocking_bot = FakeBot(forbidden_for={4001})
    assert not await try_send_dm(blocking_bot, session, user, "x", kind="teste")
    await session.commit()
    user = await repo.get_user_by_tg(session, 4001)
    assert user.dm_blocked
    with pytest.raises(DmSkip):
        await check_can_dm(session, user)


async def test_apagar_meus_dados_remove_tudo(session: AsyncSession):
    uid = await _user(session, 5001)
    product_id = await _product(session)
    await repo.toggle_follow(session, uid, "one piece", FREE_FOLLOWS)
    await repo.upsert_price_alert(session, uid, product_id, Decimal("40"), FREE_ALERTS)
    await repo.log_dm(session, uid, "teste")
    await session.commit()

    assert await repo.delete_user_data(session, 5001)
    await session.commit()
    assert await repo.get_user_by_tg(session, 5001) is None
    assert await repo.count_active_follows(session, uid) == 0
    assert await repo.count_active_alerts(session, uid) == 0


# ------------------------------------------------------------------ puro


def test_normalize_franchise():
    assert repo.normalize_franchise("  One   Piece ") == "one piece"
    assert repo.toggle_follow  # sanity


async def test_track_botao_cria_alerta_no_post(session: AsyncSession):
    """Fluxo do botão do canal: alerta no product do post, alvo = preço postado."""
    uid = await _user(session, 6001)
    product_id = await _product(session)
    post = PostQueue(
        product_id=product_id, price_at_post=Decimal("49.90"), reason="novo no radar",
        message_text="txt", affiliate_link="https://s.shopee.com.br/x", sub_id="p1xabc",
        status=PostStatus.POSTED, posted_at=NOW,
    )
    session.add(post)
    await session.commit()

    # simula cb_track: alvo = price_at_post (dispara quando ficar mais barato)
    alert, status = await repo.upsert_price_alert(
        session, uid, post.product_id, post.price_at_post, FREE_ALERTS
    )
    await session.commit()
    assert status == "created" and alert.target_price == Decimal("49.90")
