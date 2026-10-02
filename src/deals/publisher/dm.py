"""Envio de DMs para usuários (fase 3a): seguidores de franquia e alertas de preço.

Regras (doc fase 3): limite de DMs/dia por usuário, quem bloqueou o bot é
marcado e não recebe mais nada, tudo registrado em dm_log.
Nunca levanta exceção — falha de uma DM não pode abortar o job.
"""

from datetime import UTC, datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.text_decorations import html_decoration
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from deals.config import settings
from deals.db import repositories as repo
from deals.db.models import User
from deals.publisher.queue import local_day_start


class DmSkip(Exception):
    """DM não enviada por regra própria (inativo/bloqueado/limite)."""


def _offer_keyboard(affiliate_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Ver oferta", url=affiliate_link)]]
    )


async def check_can_dm(session: AsyncSession, user: User) -> None:
    """Regras de envio. Levanta DmSkip com o motivo."""
    if not user.is_active:
        raise DmSkip("usuário parou as notificações (/parar)")
    if user.dm_blocked:
        raise DmSkip("usuário bloqueou o bot")
    day_start = local_day_start(datetime.now(UTC), settings.TIMEZONE)
    sent_today = await repo.count_dms_since(session, user.id, day_start)
    if sent_today >= settings.MAX_DM_PER_USER_DAY:
        raise DmSkip(f"limite diário de DMs atingido ({settings.MAX_DM_PER_USER_DAY})")


async def try_send_dm(
    bot: Bot,
    session: AsyncSession,
    user: User,
    text: str,
    *,
    affiliate_link: str | None = None,
    kind: str,
    post_id: int | None = None,
) -> bool:
    """Envia DM com regras. Retorna True se enviada; nunca levanta exceção."""
    try:
        await check_can_dm(session, user)
    except DmSkip as exc:
        logger.debug("DM pulada p/ user {}: {}", user.tg_id, exc)
        return False

    try:
        await bot.send_message(
            chat_id=user.tg_id,
            text=html_decoration.quote(text),
            parse_mode=ParseMode.HTML,
            reply_markup=_offer_keyboard(affiliate_link) if affiliate_link else None,
            disable_web_page_preview=True,
        )
        await repo.log_dm(session, user.id, kind, post_id)
        return True
    except TelegramForbiddenError:
        logger.info("User {} bloqueou o bot — marcado p/ não receber mais DMs", user.tg_id)
        await repo.mark_user_blocked(session, user.tg_id)
        return False
    except TelegramRetryAfter as exc:
        logger.warning(
            "Flood control em DM p/ user {}: aguardando {}s (sem retry neste ciclo)",
            user.tg_id, exc.retry_after,
        )
        return False
    except TelegramAPIError as exc:
        logger.warning("Erro Telegram ao mandar DM p/ user {}: {}", user.tg_id, exc.message)
        return False
