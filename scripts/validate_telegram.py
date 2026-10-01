"""Validação manual: bot Telegram válido e admin do canal?

Uso: uv run python scripts/validate_telegram.py
"""

import asyncio

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from deals.config import settings


async def main() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        me = await bot.get_me()
        print(f"bot: @{me.username} (id={me.id})")

        try:
            chat = await bot.get_chat(settings.CHANNEL_ID)
            print(f"canal: {chat.title!r} (id={chat.id})")
        except TelegramAPIError as exc:
            print(f"ERRO ao acessar o canal: {exc.message}")
            return

        member = await bot.get_chat_member(settings.CHANNEL_ID, me.id)
        status = member.status
        print(f"status do bot no canal: {status}")
        if status != ChatMemberStatus.ADMINISTRATOR:
            print("ERRO: o bot precisa ser ADMINISTRADOR do canal (Fase 0 da spec)")
            return
        can_post = getattr(member, "can_post_messages", True)
        print(f"pode postar mensagens: {can_post}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
