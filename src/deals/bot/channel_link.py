"""Resolve o link de entrada do canal (p/ botão no /start e comando /canal).

- Canal PÚBLICO: https://t.me/<username>
- Canal PRIVADO: cria um invite link com o bot (admin) e cacheia em
  bot_settings (não cria um link novo a cada /start).
"""

from aiogram import Bot
from loguru import logger

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal

SETTING_CHANNEL_LINK = "channel_invite_link"


async def get_channel_link(bot: Bot) -> str | None:
    """Link de entrada do canal. None se o bot não tiver acesso."""
    chat = await bot.get_chat(settings.CHANNEL_ID)
    if chat.username:
        return f"https://t.me/{chat.username}"

    async with AsyncSessionLocal() as session:
        cached = await repo.get_setting(session, SETTING_CHANNEL_LINK)
        if cached:
            return cached

        invite = await bot.create_chat_invite_link(settings.CHANNEL_ID, name="link-do-bot")
        await repo.set_setting(session, SETTING_CHANNEL_LINK, invite.invite_link)
        await session.commit()
    logger.info("Invite link do canal criado e cacheado ({})", invite.invite_link)
    return invite.invite_link
