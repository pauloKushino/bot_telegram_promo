"""Handlers públicos (fase 3a): /start, seguir franquias, alertas, privacidade.

Callbacks (64 bytes máx. do Telegram):
  fr:<hash12>          toggle seguir franquia (hash = md5 da franquia normalizada)
  track:<post_id>      botão "Acompanhar este item" nos posts do canal
  alertp:<product_id>:<pct>  ajusta alvo p/ pct% abaixo do preço postado
"""

import hashlib
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal
from deals.engine.deals import format_brl

router = Router(name="user")

MAX_CALLBACK_DATA = 64


def fr_hash(franchise: str) -> str:
    return hashlib.md5(repo.normalize_franchise(franchise).encode()).hexdigest()[:12]


def franchise_keyboard(franchises: list[str], following: set[str]) -> InlineKeyboardMarkup:
    buttons = []
    for fr in franchises:
        mark = "✅ " if repo.normalize_franchise(fr) in following else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{fr}", callback_data=f"fr:{fr_hash(fr)}")])
    if not buttons:
        buttons.append(
            [InlineKeyboardButton(text="(nenhuma franquia detectada ainda)", callback_data="noop")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _user_keyboard(user_tg_id: int) -> InlineKeyboardMarkup:
    async with AsyncSessionLocal() as session:
        franchises = await repo.list_available_franchises(session)
        user = await repo.get_user_by_tg(session, user_tg_id)
        following: set[str] = set()
        if user is not None:
            following = {f.franchise for f in await repo.get_user_follows(session, user.id)}
    return franchise_keyboard(franchises, following)


WELCOME = (
    "👋 Bem-vindo ao AniPromo!\n\n"
    "Eu monitoro preços de mangás, figures e Blu-rays de anime na Shopee e posto no canal "
    "quando o desconto é de verdade (com histórico de preço).\n\n"
    "Toque nas franquias para <b>seguir/deixar de seguir</b> e receber DM quando sair oferta "
    f"(grátis: até {settings.FREE_MAX_FOLLOWS} franquias).\n"
    f"Você também pode acompanhar o preço de um item pelo botão 🔔 nos posts do canal "
    f"(grátis: {settings.FREE_MAX_ALERTS} alerta ativo).\n\n"
    "Comandos: /parar (parar DMs), /apagar_meus_dados (apagar tudo)."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    tg = message.from_user
    if tg is None:
        return
    async with AsyncSessionLocal() as session:
        await repo.get_or_create_user(session, tg.id, tg.username, tg.full_name)
        await session.commit()
    kb = await _user_keyboard(tg.id)
    await message.answer(WELCOME, reply_markup=kb)
    logger.info("Novo /start de {} ({})", tg.full_name, tg.id)


@router.callback_query(lambda c: c.data == "noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("fr:"))
async def cb_toggle_follow(query: CallbackQuery) -> None:
    tg = query.from_user
    wanted_hash = (query.data or "")[3:]

    async with AsyncSessionLocal() as session:
        user = await repo.get_or_create_user(session, tg.id, tg.username, tg.full_name)
        franchises = await repo.list_available_franchises(session)
        franchise = next((f for f in franchises if fr_hash(f) == wanted_hash), None)
        if franchise is None:
            await query.answer(
                "Franquia não encontrada (a lista mudou). Mande /start de novo.", show_alert=True
            )
            return

        following, msg = await repo.toggle_follow(session, user.id, franchise, settings.FREE_MAX_FOLLOWS)
        await session.commit()

    kb = await _user_keyboard(tg.id)
    try:
        await query.message.edit_reply_markup(reply_markup=kb)
    except Exception:  # noqa: BLE001 - teclado idêntico/velho: ok ignorar
        pass
    await query.answer(msg, show_alert=not following or "Limite" in msg)
    logger.info("Follow toggle: user={} franchise={} seguindo={}", tg.id, franchise, following)


@router.callback_query(lambda c: c.data and c.data.startswith("track:"))
async def cb_track(query: CallbackQuery) -> None:
    """Botão nos posts do canal: cria alerta 'me avise se ficar mais barato'."""
    tg = query.from_user
    try:
        post_id = int((query.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Botão inválido.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        user = await repo.get_user_by_tg(session, tg.id)
        if user is None:
            await query.answer(
                "Para receber alertas, abra uma conversa comigo e mande /start primeiro 🙂",
                show_alert=True,
            )
            return

        from deals.db.models import PostQueue

        post = await session.get(PostQueue, post_id)
        if post is None:
            await query.answer("Oferta não encontrada (já expirou?).", show_alert=True)
            return

        # alvo = preço postado: dispara quando o preço cair ABAIXO dele
        alert, status = await repo.upsert_price_alert(
            session, user.id, post.product_id, post.price_at_post, settings.FREE_MAX_ALERTS
        )
        await session.commit()

    if alert is None:
        await query.answer(
            f"Limite do plano grátis: {settings.FREE_MAX_ALERTS} alerta ativo. "
            "Espere ele disparar — ou aguarde o plano premium 😉",
            show_alert=True,
        )
        return

    if status == "updated":
        await query.answer("Alerta atualizado para este item! 🔔")
    else:
        await query.answer("Alerta criado! Te aviso por DM se o preço cair. 🔔")
        await _send_track_confirmation(query, post)
    logger.info("Track: user={} post={} status={}", tg.id, post_id, status)


async def _send_track_confirmation(query: CallbackQuery, post) -> None:
    """DM de confirmação com opções rápidas de preço-alvo abaixo do postado."""
    preco = post.price_at_post
    botoes = [
        [
            InlineKeyboardButton(
                text=f"Se cair 5% ({format_brl(_pct(preco, 5))})",
                callback_data=f"alertp:{post.product_id}:5",
            ),
            InlineKeyboardButton(
                text=f"10% ({format_brl(_pct(preco, 10))})",
                callback_data=f"alertp:{post.product_id}:10",
            ),
            InlineKeyboardButton(
                text=f"20% ({format_brl(_pct(preco, 20))})",
                callback_data=f"alertp:{post.product_id}:20",
            ),
        ]
    ]
    texto = (
        f"🔔 Alerta criado!\n\nVou te avisar se o preço cair abaixo de {format_brl(preco)} "
        "(o valor do post).\n\nQuer um alvo mais agressivo? Toque numa opção:"
    )
    await query.bot.send_message(
        tg_id_safe(query),
        texto,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=botoes),
    )


def tg_id_safe(query: CallbackQuery) -> int:
    return query.from_user.id


def _pct(preco: Decimal, pct: int) -> Decimal:
    return (preco * Decimal(100 - pct) / 100).quantize(Decimal("0.01"))


@router.callback_query(lambda c: c.data and c.data.startswith("alertp:"))
async def cb_alert_target(query: CallbackQuery) -> None:
    tg = query.from_user
    try:
        _, product_id_s, pct_s = (query.data or "").split(":")
        product_id, pct = int(product_id_s), int(pct_s)
    except ValueError:
        await query.answer("Opção inválida.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        user = await repo.get_user_by_tg(session, tg.id)
        if user is None:
            await query.answer("Mande /start primeiro 🙂", show_alert=True)
            return
        post_last = await repo.get_last_posted_for_product(session, product_id)
        if post_last is None:
            await query.answer("Oferta não encontrada.", show_alert=True)
            return
        alvo = _pct(post_last.price_at_post, pct)
        alert, status = await repo.upsert_price_alert(
            session, user.id, product_id, alvo, settings.FREE_MAX_ALERTS
        )
        await session.commit()

    if alert is None:
        await query.answer(
            f"Limite do plano grátis: {settings.FREE_MAX_ALERTS} alerta ativo.", show_alert=True
        )
        return
    await query.answer(
        f"Alvo ajustado: aviso quando cair abaixo de {format_brl(alvo)} 🎯", show_alert=True
    )


@router.message(Command("parar"))
async def cmd_parar(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as session:
        ok = await repo.deactivate_user(session, message.from_user.id)
        await session.commit()
    if ok:
        await message.answer("🛑 Pronto, não te mando mais DMs. Para voltar é só mandar /start.")
    else:
        await message.answer("Você ainda não está cadastrado. Mande /start se quiser começar.")


@router.message(Command("apagar_meus_dados"))
async def cmd_apagar(message: Message) -> None:
    if message.from_user is None:
        return
    async with AsyncSessionLocal() as session:
        ok = await repo.delete_user_data(session, message.from_user.id)
        await session.commit()
    if ok:
        await message.answer(
            "🗑️ Seus dados foram apagados (franquias, alertas e histórico de DMs). "
            "Se quiser voltar um dia, é só mandar /start."
        )
    else:
        await message.answer("Não encontrei nenhum dado seu.")
