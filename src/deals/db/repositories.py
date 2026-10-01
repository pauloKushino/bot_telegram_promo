"""Acesso a dados. Funções pequenas que recebem uma AsyncSession.

Nada aqui faz commit próprio além do necessário para a operação; chamadores
(jobs, handlers) controlam a sessão com `async with AsyncSessionLocal() as s`.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete as sql_delete
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deals.db.models import (
    SETTING_PAUSED,
    BlacklistTerm,
    BotSetting,
    Keyword,
    PostQueue,
    PostStatus,
    PriceHistory,
    Product,
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


async def count_pending_posts(session: AsyncSession) -> int:
    return (
        await session.scalar(select(func.count(PostQueue.id)).where(PostQueue.status == PostStatus.PENDING))
    ) or 0


async def clear_table_for_tests(session: AsyncSession) -> None:  # pragma: no cover - utilitário
    for table in (PostQueue, PriceHistory, Product, Keyword, BlacklistTerm, BotSetting):
        await session.execute(sql_delete(table))
