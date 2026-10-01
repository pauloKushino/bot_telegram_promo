"""Jobs do worker (spec 4): collect → classify → build_queue → publish.

Regras:
- Erros de API externa nunca derrubam o processo: tratar, logar e seguir.
- Cada job abre e fecha a própria sessão de banco.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger
from sqlalchemy import func, select

from deals.ai.classifier import ClassifierError, ProductInput, classify_products
from deals.ai.client import LLMClient, LLMError, get_llm
from deals.ai.copywriter import CopyContext, CopywriterError, generate_caption
from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal
from deals.db.models import PostQueue, PostStatus, Product
from deals.engine.deals import (
    PricePoint,
    evaluate_deal,
    format_brl,
    has_enough_history,
    is_repost_blocked,
)
from deals.engine.filters import apply_filters
from deals.publisher.queue import PublishLimits, can_publish, local_day_start
from deals.publisher.telegram import TelegramPublishError, send_post
from deals.stores import get_enabled_adapters
from deals.stores.base import RawOffer, StoreAdapter

# Produtos novos/sem histórico: postar como "novo no radar", sem alegar desconto
# (spec fase 1). Limite diário configurável por env (MAX_RADAR_POSTS_PER_DAY).
RADAR_REASON = "novo no radar: primeiro registro deste produto, sem histórico de preço"
DEACTIVATE_AFTER_DAYS = 7

# ---------------------------------------------------------------- collect


async def collect_job(adapters: list[StoreAdapter] | None = None) -> str | None:
    """Coleta ofertas de cada keyword ativa em cada loja habilitada."""
    adapters = adapters if adapters is not None else get_enabled_adapters()
    if not adapters:
        logger.warning("Nenhum adapter de loja habilitado; coleta pulada")
        return "nenhum adapter habilitado"

    total_new, total_updated = 0, 0
    async with AsyncSessionLocal() as session:
        keywords = await repo.get_active_keywords(session)
        if not keywords:
            logger.warning("Nenhuma keyword ativa; coleta pulada")

        for keyword in keywords:
            for adapter in adapters:
                if keyword.store is not None and keyword.store.value != adapter.name:
                    continue
                offers = await adapter.search(keyword.term, limit=20)
                for offer in offers:
                    product, created = await repo.upsert_product(
                        session,
                        store=offer.store,
                        external_id=offer.external_id,
                        title=offer.title,
                        url=offer.url,
                        image_url=offer.image_url,
                        seller_name=offer.seller_name,
                        seller_rating=offer.seller_rating,
                        sales_count=offer.sales_count,
                        commission_rate=offer.commission_rate,
                    )
                    await repo.maybe_insert_price(session, product.id, offer.price)
                    total_new += 1 if created else 0
                    total_updated += 0 if created else 1
                await session.commit()  # commit por loja/keyword: falha parcial não perde tudo

        deactivated = await repo.deactivate_unseen_products(session, days=DEACTIVATE_AFTER_DAYS)
        await session.commit()
    logger.info(
        "Coleta: {} novos, {} atualizados, {} desativados (>{}d sem aparecer)",
        total_new, total_updated, deactivated, DEACTIVATE_AFTER_DAYS,
    )
    return f"{total_new} novos, {total_updated} atualizados, {deactivated} desativados"


async def _latest_price(session, product_id: int) -> Decimal | None:
    history = await repo.get_price_history(session, product_id, since=datetime.now(UTC) - timedelta(days=1))
    return history[-1].price if history else None


# ---------------------------------------------------------------- classify


async def classify_job(llm: LLMClient | None = None) -> str | None:
    """Classifica produtos novos com IA (resultado cacheado no próprio produto)."""
    llm = llm or get_llm()
    async with AsyncSessionLocal() as session:
        products = await repo.get_unclassified_products(session, limit=50)
        if not products:
            logger.debug("Classificação: nada pendente")
            return "nada pendente"

        inputs = []
        for p in products:
            price = await _latest_price(session, p.id)
            inputs.append(
                ProductInput(
                    id=p.id,
                    title=p.title,
                    seller_name=p.seller_name,
                    price=price or Decimal("0"),
                    store=p.store.value,
                )
            )
        try:
            results = await classify_products(llm, inputs)
        except (ClassifierError, LLMError) as exc:
            logger.error("Classificação falhou neste ciclo (produtos ficam p/ o próximo): {}", exc)
            return f"falhou: {exc}"

        for product_id, cls in results.items():
            await repo.apply_classification(
                session,
                product_id,
                is_anime_merch=cls.is_anime_merch,
                franchise=cls.franchise,
                product_type=cls.product_type,
                volume=cls.volume,
                publisher=cls.publisher,
                official_confidence=cls.official_confidence,
            )
        await session.commit()
    logger.info("Classificação: {} produtos classificados", len(results))
    return f"{len(results)} classificados"


# ---------------------------------------------------------------- build queue


async def build_queue_job(
    llm: LLMClient | None = None, adapters: list[StoreAdapter] | None = None
) -> str | None:
    """Deal engine + copywriter: transforma candidatos em posts na fila."""
    llm = llm or get_llm()
    adapters_by_name = {a.name: a for a in (adapters if adapters is not None else get_enabled_adapters())}
    now = datetime.now(UTC)

    enqueued = 0
    async with AsyncSessionLocal() as session:
        blacklist = await repo.get_active_blacklist_terms(session)
        radar_today = await _count_radar_posts_today(session, now)

        candidates = await _deal_candidates(session)
        for product in candidates:
            try:
                price = await _latest_price(session, product.id)
                if price is None:
                    continue  # sem nenhum preço coletado (não deveria acontecer)

                filt = apply_filters(
                    product, price, blacklist,
                    min_seller_rating=settings.MIN_SELLER_RATING,
                    min_sales_count=settings.MIN_SALES_COUNT,
                    min_official_confidence=settings.MIN_OFFICIAL_CONFIDENCE,
                )
                if not filt.ok:
                    logger.debug("Produto {} barrado: {}", product.id, "; ".join(filt.reasons))
                    continue

                if await repo.has_open_post(session, product.id):
                    continue

                last_post = await repo.get_last_posted_for_product(session, product.id)
                if last_post and is_repost_blocked(
                    last_post.price_at_post, last_post.posted_at, price, now=now
                ):
                    logger.debug("Produto {}: repost dentro de 72h sem queda >=10%", product.id)
                    continue

                history = [
                    PricePoint(price=h.price, collected_at=h.collected_at)
                    for h in await repo.get_price_history(session, product.id, since=now - timedelta(days=60))
                ]
                verdict = evaluate_deal(
                    history, now=now,
                    min_history_days=settings.MIN_HISTORY_DAYS,
                    min_drop_pct=settings.MIN_DROP_PCT,
                )

                enough_history = has_enough_history(history, now, settings.MIN_HISTORY_DAYS)
                is_radar = not verdict.is_deal and not enough_history
                if verdict.is_deal:
                    reason = verdict.reason
                    reference = verdict.reference_price
                elif is_radar and radar_today < settings.MAX_RADAR_POSTS_PER_DAY:
                    reason = RADAR_REASON
                    reference = None
                    radar_today += 1
                else:
                    continue

                await _enqueue_candidate(
                    session, llm, adapters_by_name, product, price,
                    reason=reason, reference_price=reference, is_radar=is_radar,
                )
                enqueued += 1
            except Exception:
                # Qualquer erro num produto não pode abortar o ciclo inteiro
                logger.exception("Erro processando produto {} na fila de ofertas", product.id)

        await session.commit()
    logger.info("Fila de ofertas: {} novos posts enfileirados", enqueued)
    return f"{enqueued} enfileirados"


async def _deal_candidates(session) -> list[Product]:
    """Ativos, classificados como merch de anime, ordenados do mais recente."""
    result = await session.scalars(
        select(Product)
        .where(Product.active.is_(True), Product.is_anime_merch.is_(True))
        .order_by(Product.last_seen_at.desc())
        .limit(500)
    )
    return list(result)


async def _count_radar_posts_today(session, now: datetime) -> int:
    """Só posts de "novo no radar" (reason específico) criados hoje, no fuso local."""
    day_start = local_day_start(now, settings.TIMEZONE)
    return (
        await session.scalar(
            select(func.count())
            .select_from(PostQueue)
            .where(PostQueue.reason == RADAR_REASON, PostQueue.created_at >= day_start)
        )
    ) or 0


async def _enqueue_candidate(
    session,
    llm: LLMClient,
    adapters_by_name: dict[str, StoreAdapter],
    product: Product,
    price: Decimal,
    *,
    reason: str,
    reference_price: Decimal | None,
    is_radar: bool,
) -> None:
    adapter = adapters_by_name.get(product.store.value)
    # sub_id curto e alfanumérico: Shopee rejeita hífens ("invalid sub id")
    sub_id = f"p{product.id}x{uuid.uuid4().hex[:8]}"  # rastreio por post

    offer = RawOffer(
        store=product.store.value,
        external_id=product.external_id,
        title=product.title,
        url=product.url,
        image_url=product.image_url,
        price=price,
    )
    affiliate_link = await adapter.build_affiliate_link(offer, sub_id) if adapter else product.url

    ctx = CopyContext(
        title=product.title,
        franchise=product.franchise,
        product_type=product.product_type.value if product.product_type else None,
        volume=product.volume,
        publisher=product.publisher,
        store=product.store.value,
        price=price,
        reason=reason,
        is_new_on_radar=is_radar,
    )
    try:
        caption = await generate_caption(llm, ctx)
    except (CopywriterError, LLMError) as exc:
        logger.warning("Copywriter rejeitou produto {}: {}", product.id, exc)
        return  # descarta o post, como manda a spec

    await repo.enqueue_post(
        session,
        product_id=product.id,
        price_at_post=price,
        reference_price=reference_price,
        reason=reason,
        message_text=caption,
        affiliate_link=affiliate_link,
        sub_id=sub_id,
    )
    logger.info("Enfileirado produto {} ({}): {}", product.id, sub_id, reason)


# ---------------------------------------------------------------- conversões e relatório (fase 2)


async def import_conversions_job() -> str | None:
    """Importa o relatório de conversões da Shopee (spec 7.7). Diário, janela de 7 dias."""
    adapters = get_enabled_adapters()
    shopee = next((a for a in adapters if a.name == "shopee"), None)
    if shopee is None or not hasattr(shopee, "fetch_conversions"):
        logger.warning("Importação de conversões: adapter Shopee indisponível")
        return None

    now = datetime.now(UTC)
    start = int((now - timedelta(days=7)).timestamp())  # janela com folga p/ atraso de atribuição
    conversions = await shopee.fetch_conversions(start, int(now.timestamp()))  # type: ignore[attr-defined]

    novas = 0
    async with AsyncSessionLocal() as session:
        for c in conversions:
            nova = await repo.upsert_conversion(
                session,
                store="shopee",
                sub_id=c.sub_id,
                order_id=c.order_id,
                commission=c.commission,
                status=c.status,
                occurred_at=datetime.fromtimestamp(c.occurred_at, UTC),
            )
            novas += 1 if nova else 0
        await session.commit()
    return f"{novas} conversões novas de {len(conversions)} importadas"


def render_daily_report(
    *,
    posts_today: int,
    new_products_today: int,
    pending_queue: int,
    conversions_today: int,
    commission_today: Decimal,
    top_posts: list[tuple[str, Decimal]],  # (link_afiliado, comissão_total)
) -> str:
    """Texto do relatório diário ao admin (HTML p/ Telegram). Função pura p/ teste."""
    linhas = [
        "📈 <b>Relatório diário</b>",
        f"Posts publicados hoje: <b>{posts_today}</b>",
        f"Produtos novos monitorados: <b>{new_products_today}</b>",
        f"Fila pendente: <b>{pending_queue}</b>",
        "",
        f"Vendas hoje: <b>{conversions_today}</b>",
        f"Comissão estimada hoje: <b>{format_brl(commission_today)}</b>",
    ]
    if top_posts:
        linhas.append("")
        linhas.append("<b>Top posts do dia (por comissão):</b>")
        for i, (link, total) in enumerate(top_posts, start=1):
            linhas.append(f"{i}. {format_brl(total)} — <a href=\"{link}\">post</a>")
    else:
        linhas.append("")
        linhas.append("Sem vendas atribuídas a posts hoje (ainda).")
    return "\n".join(linhas)


async def daily_report_job(bot: Bot | None = None) -> str | None:
    """Envia o relatório diário por DM para cada admin (spec 7.7)."""
    own_bot = bot is None
    if own_bot:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    now = datetime.now(UTC)
    day_start = local_day_start(now, settings.TIMEZONE)

    try:
        async with AsyncSessionLocal() as session:
            posts_today = await repo.count_posted_between(session, day_start, now)
            new_products = await repo.count_products_since(session, day_start)
            pending = await repo.count_pending_posts(session)
            conversions = await repo.conversions_between(session, day_start, now)
            commission = await repo.commission_sum_between(session, day_start, now)
            tops = await repo.top_posts_by_commission(session, day_start, now)

        texto = render_daily_report(
            posts_today=posts_today,
            new_products_today=new_products,
            pending_queue=pending,
            conversions_today=len(conversions),
            commission_today=commission,
            top_posts=[(p.affiliate_link, total) for p, total in tops],
        )

        enviados = 0
        for admin_id in settings.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(admin_id, texto, disable_web_page_preview=True)
                enviados += 1
            except Exception:
                logger.exception("Relatório diário: falha ao enviar DM p/ admin {}", admin_id)
        logger.info("Relatório diário enviado a {}/{} admins", enviados, len(settings.ADMIN_TELEGRAM_IDS))
        return f"relatório p/ {enviados} admin(s)"
    finally:
        if own_bot:
            await bot.session.close()


# ---------------------------------------------------------------- publish


async def publish_job(bot: Bot | None = None) -> str | None:
    """Publica o próximo post da fila respeitando pausa, silêncio e limites."""
    own_bot = bot is None
    if own_bot:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    limits = PublishLimits.from_quiet_hours(
        settings.QUIET_HOURS,
        max_posts_per_day=settings.MAX_POSTS_PER_DAY,
        min_post_interval_min=settings.MIN_POST_INTERVAL_MIN,
        timezone=settings.TIMEZONE,
    )
    now = datetime.now(UTC)

    try:
        async with AsyncSessionLocal() as session:
            paused = await repo.is_paused(session)
            posts_today = await repo.count_posted_between(
                session, local_day_start(now, settings.TIMEZONE), now + timedelta(seconds=1)
            )
            last_posted_at = await repo.get_last_posted_at(session)

            decision = can_publish(
                now, posts_today=posts_today, last_posted_at=last_posted_at, paused=paused, limits=limits
            )
            if not decision.allowed:
                logger.debug("Publish pulado: {}", decision.reason)
                return None

            post = await repo.pop_due_post(session, now)
            if post is None:
                logger.debug("Publish: fila vazia")
                return None

            product = await session.get(Product, post.product_id)
            if product is None:
                await repo.mark_post(session, post, PostStatus.SKIPPED)
                await session.commit()
                return None

            try:
                message_id = await send_post(bot, settings.CHANNEL_ID, post, product)
            except TelegramPublishError as exc:
                logger.error("Falha ao publicar post {}: {}", post.id, exc)
                await repo.mark_post(session, post, PostStatus.FAILED)
                await session.commit()
                return None

            await repo.mark_post(session, post, PostStatus.POSTED, telegram_message_id=message_id)
            await session.commit()
            logger.info("Publicado post {} (produto {}) msg_id={}", post.id, post.product_id, message_id)
            return f"publicado post {post.id} (produto {post.product_id})"
    finally:
        if own_bot:
            await bot.session.close()
