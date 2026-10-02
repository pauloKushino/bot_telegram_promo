"""Publicação no canal Telegram (spec 7.5).

Formato do post: foto do produto + legenda + botão inline "Ver oferta".
Tolerâncias: flood control (retry_after), foto inválida → postar sem foto,
legenda longa → truncar. Erros sobem como TelegramPublishError p/ o job marcar
o post como failed sem derrubar o worker.
"""

import asyncio

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.text_decorations import html_decoration
from loguru import logger

from deals.db.models import PostQueue, Product

CAPTION_LIMIT = 1024
BUTTON_TEXT = "Ver oferta"


class TelegramPublishError(Exception):
    """Falha não recuperável ao publicar (post → failed)."""


def _keyboard(affiliate_link: str, post_id: int | None = None) -> InlineKeyboardMarkup:
    """Botão "Ver oferta" + (fase 3a) "🔔 Acompanhar este item"."""
    rows = [[InlineKeyboardButton(text=BUTTON_TEXT, url=affiliate_link)]]
    if post_id is not None:
        rows.append(
            [InlineKeyboardButton(text="🔔 Acompanhar este item", callback_data=f"track:{post_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _safe_caption(text: str) -> str:
    if len(text) <= CAPTION_LIMIT:
        return text
    return text[: CAPTION_LIMIT - 1] + "…"


async def send_post(bot: Bot, channel_id: int, post: PostQueue, product: Product) -> int:
    """Publica e retorna o message_id. Levanta TelegramPublishError em falha."""
    caption = _safe_caption(html_decoration.quote(post.message_text))
    markup = _keyboard(post.affiliate_link, post_id=post.id)

    for _ in range(2):  # 1 tentativa + 1 retry p/ flood control
        try:
            message = None
            if product.image_url:
                try:
                    message = await bot.send_photo(
                        chat_id=channel_id,
                        photo=product.image_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                except TelegramBadRequest as photo_exc:
                    logger.warning(
                        "Foto inválida do produto {} ({}); postando sem foto",
                        product.id, photo_exc.message,
                    )
            if message is None:
                message = await bot.send_message(
                    chat_id=channel_id,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            return message.message_id
        except TelegramRetryAfter as exc:
            logger.warning("Flood control do Telegram: aguardando {}s", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except TelegramBadRequest as exc:
            raise TelegramPublishError(f"Telegram recusou o post {post.id}: {exc.message}") from exc

    raise TelegramPublishError(f"post {post.id} falhou após retry de flood control")
