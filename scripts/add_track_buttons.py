"""Manutenção: adiciona o botão "🔔 Acompanhar este item" aos posts já publicados.

Uso: uv run python scripts/add_track_buttons.py
Idempotente: pula posts cujo teclado já está correto (edit igual é no-op).
"""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger
from sqlalchemy import select

from deals.config import settings
from deals.db.base import AsyncSessionLocal
from deals.db.models import PostQueue, PostStatus
from deals.publisher.telegram import _keyboard


async def main() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        async with AsyncSessionLocal() as s:
            posts = (
                await s.scalars(
                    select(PostQueue).where(
                        PostQueue.status == PostStatus.POSTED,
                        PostQueue.telegram_message_id.is_not(None),
                    )
                )
            ).all()

        alterados = 0
        for p in posts:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=settings.CHANNEL_ID,
                    message_id=p.telegram_message_id,
                    reply_markup=_keyboard(p.affiliate_link, post_id=p.id),
                )
                alterados += 1
                logger.info("post #{} (msg {}): botão adicionado", p.id, p.telegram_message_id)
            except TelegramAPIError as exc:
                logger.warning("post #{}: {}", p.id, exc.message)
        print(f"{alterados}/{len(posts)} posts atualizados com o botão de acompanhar")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
