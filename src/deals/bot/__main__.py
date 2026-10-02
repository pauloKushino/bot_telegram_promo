"""Entrada do bot: `python -m deals.bot`.

Polling (sem webhook). Dois routers:
- admin: comandos restritos a ADMIN_TELEGRAM_IDS (/stats /pause ...)
- user: público (fase 3a): /start, seguir franquias, alertas, /parar.
"""

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from deals.bot.handlers.admin import router as admin_router
from deals.bot.handlers.user import router as user_router
from deals.config import settings
from deals.logging import setup_logging


async def main() -> None:
    setup_logging()
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(user_router)

    logger.info("Bot iniciando (polling; {} admin(s) configurado(s))", len(settings.ADMIN_TELEGRAM_IDS))
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
