"""Acesso a dados. Funções pequenas que recebem uma AsyncSession.

Nada aqui faz commit próprio além do necessário para a operação; chamadores
(jobs, handlers) controlam a sessão com `async with AsyncSessionLocal() as s`.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete as sql_delete
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deals.db.models import (
    SETTING_PAUSED,
    BlacklistTerm,
    BotSetting,
    Conversion,
    DmLog,
    Follow,
    Keyword,
    PostQueue,
    PostStatus,
    PriceAlert,
    PriceHistory,
    Product,
    User,
)

# ---------------------------------------------------------------- products


async def upsert_product(
    session: AsyncSession,
    *,
    store: str,
    external_id: str,
    title: str,
    url: str,
    image_url: str | None,
    seller_name: str | None,
    seller_rating: float | None,
    sales_count: int | None,
    commission_rate: float | None,
) -> tuple[Product, bool]:
    """Cria ou atualiza um produto por (store, external_id).

    Retorna (produto, criado_agora). Atualiza campos mutáveis e last_seen_at.
    """
    now = datetime.now(UTC)
    stmt = select(Product).where(Product.store == store, Product.external_id == external_id)
    product = await session.scalar(stmt)

    if product is None:
        product = Product(
            store=store,
            external_id=external_id,
            title=title,
            url=url,
            image_url=image_url,
            seller_name=seller_name,
            seller_rating=seller_rating,
            sales_count=sales_count,
            commission_rate=commission_rate,
            active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(product)
        await session.flush()  # garante product.id
        return product, True

    product.title = title
    product.url = url
    product.image_url = image_url
    product.seller_name = seller_name
    product.seller_rating = seller_rating
    product.sales_count = sales_count
    product.commission_rate = commission_rate
    product.last_seen_at = now
    return product, False


async def maybe_insert_price(
    session: AsyncSession,
    product_id: int,
    price: Decimal,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    min_interval_hours: int = 6,
) -> bool:
    """Insere linha em price_history só se o preço mudou ou passou `min_interval_hours`.

    Evita inchar a tabela com preços repetidos a cada ciclo de coleta.
    """
    last = await session.scalar(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(desc(PriceHistory.collected_at))
        .limit(1)
    )
    now = datetime.now(UTC)
    if last is not None:
        same_price = last.price == price
        recent = last.collected_at > now - timedelta(hours=min_interval_hours)
        if same_price and recent:
            return False

    session.add(
        PriceHistory(
            product_id=product_id, price=price, price_min=price_min, price_max=price_max, collected_at=now
        )
    )
    return True


async def deactivate_unseen_products(session: AsyncSession, days: int = 7) -> int:
    """Marca active=false em produtos não vistos há `days` dias. Retorna quantos."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        update(Product).where(Product.active.is_(True), Product.last_seen_at < cutoff).values(active=False)
    )
    return result.rowcount  # type: ignore[return-value]


async def get_price_history(session: AsyncSession, product_id: int, since: datetime) -> list[PriceHistory]:
    """Histórico de preços desde `since`, do mais antigo ao mais novo."""
    result = await session.scalars(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id, PriceHistory.collected_at >= since)
        .order_by(PriceHistory.collected_at)
    )
    return list(result)


# ---------------------------------------------------------------- classificação


async def get_unclassified_products(session: AsyncSession, limit: int = 50) -> list[Product]:
    """Produtos ativos ainda não classificados (is_anime_merch IS NULL)."""
    result = await session.scalars(
        select(Product)
        .where(Product.active.is_(True), Product.is_anime_merch.is_(None))
        .order_by(Product.first_seen_at)
        .limit(limit)
    )
    return list(result)


async def apply_classification(
    session: AsyncSession,
    product_id: int,
    *,
    is_anime_merch: bool,
    franchise: str | None,
    product_type: str,
    volume: str | None,
    publisher: str | None,
    official_confidence: float,
) -> None:
    product = await session.get(Product, product_id)
    if product is None:
        return
    product.is_anime_merch = is_anime_merch
    product.franchise = franchise
    product.product_type = product_type
    product.volume = volume
    product.publisher = publisher
    product.official_confidence = official_confidence
    product.classified_at = datetime.now(UTC)


# ---------------------------------------------------------------- keywords / blacklist


async def get_active_keywords(session: AsyncSession) -> list[Keyword]:
    result = await session.scalars(select(Keyword).where(Keyword.active.is_(True)).order_by(Keyword.id))
    return list(result)


async def add_keyword(session: AsyncSession, term: str, store: str | None = None) -> Keyword:
    """Idempotente: keyword ativa com o mesmo termo é retornada sem duplicar."""
    existing = await session.scalar(
        select(Keyword).where(Keyword.term == term.strip(), Keyword.active.is_(True))
    )
    if existing is not None:
        return existing
    kw = Keyword(term=term.strip(), store=store, active=True)
    session.add(kw)
    await session.flush()
    return kw


async def deactivate_keyword(session: AsyncSession, term: str) -> bool:
    result = await session.execute(
        update(Keyword).where(Keyword.term == term.strip(), Keyword.active.is_(True)).values(active=False)
    )
    return (result.rowcount or 0) > 0


async def get_active_blacklist_terms(session: AsyncSession) -> list[str]:
    result = await session.scalars(
        select(BlacklistTerm.term).where(BlacklistTerm.active.is_(True)).order_by(BlacklistTerm.id)
    )
    return list(result)


async def add_blacklist_term(session: AsyncSession, term: str) -> BlacklistTerm:
    """Idempotente: termo existente (único no banco) é apenas reativado."""
    existing = await session.scalar(select(BlacklistTerm).where(BlacklistTerm.term == term.strip().lower()))
    if existing is not None:
        existing.active = True
        return existing
    bt = BlacklistTerm(term=term.strip().lower(), active=True)
    session.add(bt)
    await session.flush()
    return bt


# ---------------------------------------------------------------- fila de posts


async def enqueue_post(
    session: AsyncSession,
    *,
    product_id: int,
    price_at_post: Decimal,
    reference_price: Decimal | None,
    reason: str,
    message_text: str,
    affiliate_link: str,
    sub_id: str,
    scheduled_for: datetime | None = None,
) -> PostQueue:
    post = PostQueue(
        product_id=product_id,
        price_at_post=price_at_post,
        reference_price=reference_price,
        reason=reason,
        message_text=message_text,
        affiliate_link=affiliate_link,
        sub_id=sub_id,
        status=PostStatus.PENDING,
        scheduled_for=scheduled_for or datetime.now(UTC),
    )
    session.add(post)
    await session.flush()
    return post


async def has_open_post(session: AsyncSession, product_id: int) -> bool:
    """Já existe post pendente para o produto?"""
    return (
        await session.scalar(
            select(PostQueue.id).where(
                PostQueue.product_id == product_id, PostQueue.status == PostStatus.PENDING
            )
        )
    ) is not None


async def get_last_posted_for_product(session: AsyncSession, product_id: int) -> PostQueue | None:
    return await session.scalar(
        select(PostQueue)
        .where(PostQueue.product_id == product_id, PostQueue.status == PostStatus.POSTED)
        .order_by(desc(PostQueue.posted_at))
        .limit(1)
    )


async def peek_next_pending_post(session: AsyncSession) -> PostQueue | None:
    """Olha o próximo post pendente sem consumir (usado pelo /preview)."""
    return await session.scalar(
        select(PostQueue)
        .where(PostQueue.status == PostStatus.PENDING)
        .order_by(PostQueue.scheduled_for, PostQueue.id)
        .limit(1)
    )


async def pop_due_post(session: AsyncSession, now: datetime) -> PostQueue | None:
    """Pega o próximo post pendente e vencido. SKIP LOCKED p/ segurança com 1 worker."""
    return await session.scalar(
        select(PostQueue)
        .where(PostQueue.status == PostStatus.PENDING, PostQueue.scheduled_for <= now)
        .order_by(PostQueue.scheduled_for, PostQueue.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def mark_post(
    session: AsyncSession,
    post: PostQueue,
    status: PostStatus,
    telegram_message_id: int | None = None,
) -> None:
    post.status = status
    if status == PostStatus.POSTED:
        post.posted_at = datetime.now(UTC)
        post.telegram_message_id = telegram_message_id
    await session.flush()


async def count_posts_since(session: AsyncSession, since: datetime, statuses: tuple[PostStatus, ...]) -> int:
    return (
        await session.scalar(
            select(func.count(PostQueue.id)).where(
                PostQueue.status.in_(statuses), PostQueue.created_at >= since
            )
        )
    ) or 0


async def count_posted_between(session: AsyncSession, start: datetime, end: datetime) -> int:
    return (
        await session.scalar(
            select(func.count(PostQueue.id)).where(
                PostQueue.status == PostStatus.POSTED,
                PostQueue.posted_at >= start,
                PostQueue.posted_at < end,
            )
        )
    ) or 0


async def get_last_posted_at(session: AsyncSession) -> datetime | None:
    return await session.scalar(
        select(PostQueue.posted_at)
        .where(PostQueue.status == PostStatus.POSTED)
        .order_by(desc(PostQueue.posted_at))
        .limit(1)
    )


# ---------------------------------------------------------------- conversões (fase 2)


async def upsert_conversion(
    session: AsyncSession,
    *,
    store: str,
    sub_id: str | None,
    order_id: str,
    commission: Decimal | None,
    status: str,
    occurred_at: datetime,
) -> bool:
    """Insere conversão nova ou atualiza status/comissão da existente (spec 7.7).

    Dedup por (store, order_id): o relatório diário pode trazer o mesmo pedido
    em janelas sobrepostas. Retorna True se era conversão nova.
    """
    existing = await session.scalar(
        select(Conversion).where(Conversion.store == store, Conversion.order_id == order_id)
    )
    if existing is not None:
        existing.status = status
        if commission is not None:
            existing.commission = commission
        if sub_id is not None:
            existing.sub_id = sub_id
        return False

    session.add(
        Conversion(
            store=store,
            sub_id=sub_id,
            order_id=order_id,
            commission=commission,
            status=status,
            occurred_at=occurred_at,
        )
    )
    return True


async def conversions_between(session: AsyncSession, start: datetime, end: datetime) -> list[Conversion]:
    result = await session.scalars(
        select(Conversion)
        .where(Conversion.occurred_at >= start, Conversion.occurred_at < end)
        .order_by(Conversion.occurred_at)
    )
    return list(result)


async def commission_sum_between(session: AsyncSession, start: datetime, end: datetime) -> Decimal:
    return (
        await session.scalar(
            select(func.coalesce(func.sum(Conversion.commission), 0)).where(
                Conversion.occurred_at >= start, Conversion.occurred_at < end
            )
        )
    ) or Decimal("0")


async def top_posts_by_commission(
    session: AsyncSession, start: datetime, end: datetime, limit: int = 3
) -> list[tuple[PostQueue, Decimal]]:
    """Posts mais rentáveis no período (join conversions.sub_id → post_queue)."""
    stmt = (
        select(PostQueue, func.sum(Conversion.commission).label("total"))
        .join(Conversion, Conversion.sub_id == PostQueue.sub_id)
        .where(Conversion.occurred_at >= start, Conversion.occurred_at < end)
        .group_by(PostQueue.id)
        .order_by(desc("total"))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


# ---------------------------------------------------------------- status dos jobs (/health)

SETTING_JOB_RUNS = "job_runs"  # JSON: {nome_do_job: {"at": iso, "ok": bool, "detail": str}}


async def record_job_run(session: AsyncSession, name: str, ok: bool, detail: str = "") -> None:
    """Registra a última execução de um job (lido pelo /health)."""
    existing = await session.get(BotSetting, SETTING_JOB_RUNS)
    runs: dict = json.loads(existing.value) if existing else {}
    runs[name] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "ok": ok, "detail": detail[:300]}
    payload = json.dumps(runs, ensure_ascii=False)

    if existing is None:
        session.add(BotSetting(key=SETTING_JOB_RUNS, value=payload))
    else:
        existing.value = payload
    await session.flush()


async def get_job_runs(session: AsyncSession) -> dict:
    value = await session.scalar(select(BotSetting.value).where(BotSetting.key == SETTING_JOB_RUNS))
    return json.loads(value) if value else {}


# ---------------------------------------------------------------- usuários / follows / alertas (fase 3a)


def normalize_franchise(name: str) -> str:
    """Forma canônica p/ comparar follows com a franquia classificada pela IA."""
    return " ".join(name.strip().lower().split())


async def get_or_create_user(session: AsyncSession, tg_id: int, username: str | None, full_name: str) -> User:
    """Registra no /start. Reativa quem volta (desfaz /parar e bloqueio anterior)."""
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is not None:
        user.username = username
        user.full_name = full_name
        user.is_active = True
        user.dm_blocked = False
        await session.flush()
        return user
    user = User(tg_id=tg_id, username=username, full_name=full_name)
    session.add(user)
    await session.flush()
    return user


async def get_user_by_tg(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def deactivate_user(session: AsyncSession, tg_id: int) -> bool:
    result = await session.execute(
        update(User).where(User.tg_id == tg_id).values(is_active=False)
    )
    await session.flush()
    return (result.rowcount or 0) > 0


async def mark_user_blocked(session: AsyncSession, tg_id: int) -> None:
    await session.execute(update(User).where(User.tg_id == tg_id).values(dm_blocked=True))
    await session.flush()


async def delete_user_data(session: AsyncSession, tg_id: int) -> bool:
    """/apagar_meus_dados: remove usuário + follows + alertas + logs (cascade)."""
    user = await get_user_by_tg(session, tg_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True


async def list_available_franchises(session: AsyncSession, limit: int = 25) -> list[str]:
    """Franquias distintas vistas em produtos classificados como anime merch."""
    rows = await session.scalars(
        select(func.distinct(Product.franchise))
        .where(Product.is_anime_merch.is_(True), Product.franchise.is_not(None))
        .order_by(Product.franchise)
        .limit(limit)
    )
    return [r for r in rows if r]


async def get_user_follows(session: AsyncSession, user_id: int) -> list[Follow]:
    result = await session.scalars(
        select(Follow).where(Follow.user_id == user_id, Follow.active.is_(True)).order_by(Follow.franchise)
    )
    return list(result)


async def count_active_follows(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count(Follow.id)).where(Follow.user_id == user_id, Follow.active.is_(True))
        )
    ) or 0


async def toggle_follow(
    session: AsyncSession, user_id: int, franchise: str, max_free: int
) -> tuple[bool, str]:
    """Alterna seguir/deixar de seguir. Retorna (agora_segue, mensagem_para_o_usuário)."""
    fr = normalize_franchise(franchise)
    existing = await session.scalar(
        select(Follow).where(Follow.user_id == user_id, Follow.franchise == fr)
    )
    if existing is not None and existing.active:
        existing.active = False
        await session.flush()
        return False, f"Você deixou de seguir {franchise}."

    count = await count_active_follows(session, user_id)
    if count >= max_free:
        return False, (
            f"Limite do plano grátis: {max_free} franquias. "
            "Deixe de seguir outra — ou aguarde o plano premium 😉"
        )

    if existing is not None:
        existing.active = True
    else:
        session.add(Follow(user_id=user_id, franchise=fr, active=True))
    await session.flush()
    return True, f"Agora você segue {franchise}! Te aviso por aqui quando sair oferta. 🔔"


async def get_followers_of_franchise(session: AsyncSession, franchise: str) -> list[User]:
    fr = normalize_franchise(franchise)
    result = await session.scalars(
        select(User)
        .join(Follow, Follow.user_id == User.id)
        .where(
            Follow.franchise == fr,
            Follow.active.is_(True),
            User.is_active.is_(True),
            User.dm_blocked.is_(False),
        )
    )
    return list(result)


async def count_active_alerts(session: AsyncSession, user_id: int) -> int:
    return (
        await session.scalar(
            select(func.count(PriceAlert.id)).where(
                PriceAlert.user_id == user_id, PriceAlert.triggered_at.is_(None)
            )
        )
    ) or 0


async def upsert_price_alert(
    session: AsyncSession, user_id: int, product_id: int, target_price: Decimal, max_free: int
) -> tuple[PriceAlert | None, str]:
    """Cria (ou atualiza o alvo de) alerta. Respeita o limite free."""
    existing = await session.scalar(
        select(PriceAlert).where(
            PriceAlert.user_id == user_id,
            PriceAlert.product_id == product_id,
            PriceAlert.triggered_at.is_(None),
        )
    )
    if existing is not None:
        existing.target_price = target_price
        await session.flush()
        return existing, "updated"

    if await count_active_alerts(session, user_id) >= max_free:
        return None, "limit"

    alert = PriceAlert(user_id=user_id, product_id=product_id, target_price=target_price)
    session.add(alert)
    await session.flush()
    return alert, "created"


async def get_active_alerts_with_products(session: AsyncSession) -> list[tuple[PriceAlert, Product, User]]:
    """Alertas não disparados de usuários ativos, já com produto e usuário."""
    result = await session.execute(
        select(PriceAlert, Product, User)
        .join(Product, Product.id == PriceAlert.product_id)
        .join(User, User.id == PriceAlert.user_id)
        .where(
            PriceAlert.triggered_at.is_(None),
            Product.active.is_(True),
            User.is_active.is_(True),
            User.dm_blocked.is_(False),
        )
    )
    return [(r[0], r[1], r[2]) for r in result.all()]


async def mark_alert_triggered(session: AsyncSession, alert: PriceAlert) -> None:
    alert.triggered_at = datetime.now(UTC)
    await session.flush()


async def count_dms_since(session: AsyncSession, user_id: int, since: datetime) -> int:
    return (
        await session.scalar(
            select(func.count(DmLog.id)).where(DmLog.user_id == user_id, DmLog.sent_at >= since)
        )
    ) or 0


async def log_dm(session: AsyncSession, user_id: int, kind: str, post_id: int | None = None) -> None:
    session.add(DmLog(user_id=user_id, kind=kind, post_id=post_id))
    await session.flush()


# ---------------------------------------------------------------- estado do bot (pausa) e stats


async def is_paused(session: AsyncSession) -> bool:
    value = await session.scalar(select(BotSetting.value).where(BotSetting.key == SETTING_PAUSED))
    return value == "1"


async def set_paused(session: AsyncSession, paused: bool) -> None:
    value = "1" if paused else "0"
    existing = await session.get(BotSetting, SETTING_PAUSED)
    if existing is None:
        session.add(BotSetting(key=SETTING_PAUSED, value=value))
    else:
        existing.value = value
    await session.flush()


async def count_active_products(session: AsyncSession) -> int:
    return (await session.scalar(select(func.count(Product.id)).where(Product.active.is_(True)))) or 0


async def count_products_since(session: AsyncSession, since: datetime) -> int:
    return (
        await session.scalar(select(func.count(Product.id)).where(Product.first_seen_at >= since))
    ) or 0


async def count_pending_posts(session: AsyncSession) -> int:
    return (
        await session.scalar(select(func.count(PostQueue.id)).where(PostQueue.status == PostStatus.PENDING))
    ) or 0


async def clear_table_for_tests(session: AsyncSession) -> None:  # pragma: no cover - utilitário
    for table in (PostQueue, PriceHistory, Product, Keyword, BlacklistTerm, BotSetting):
        await session.execute(sql_delete(table))
