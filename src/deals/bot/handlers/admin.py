"""Comandos de administração (spec 7.6), restritos por ADMIN_TELEGRAM_IDS.

Fase 1: /stats, /pause, /resume, /preview.
Fase 2: /health, /blacklist, /addkeyword, /removekeyword.
"""

from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from loguru import logger

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal
from deals.publisher.queue import local_day_start

router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in settings.ADMIN_TELEGRAM_IDS


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        return

    now = datetime.now(UTC)
    day_start = local_day_start(now, settings.TIMEZONE)
    week_start = day_start - timedelta(days=7)

    async with AsyncSessionLocal() as session:
        posts_hoje = await repo.count_posted_between(session, day_start, now)
        posts_semana = await repo.count_posted_between(session, week_start, now)
        na_fila = await repo.count_pending_posts(session)
        produtos = await repo.count_active_products(session)
        pausado = await repo.is_paused(session)

    await message.answer(
        "📊 <b>Stats</b>\n"
        f"Posts hoje: <b>{posts_hoje}</b> | semana: <b>{posts_semana}</b>\n"
        f"Fila pendente: <b>{na_fila}</b>\n"
        f"Produtos monitorados: <b>{produtos}</b>\n"
        f"Publicação: {'⏸️ pausada' if pausado else '▶️ ativa'}"
    )


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    if not _is_admin(message):
        return
    async with AsyncSessionLocal() as session:
        await repo.set_paused(session, True)
        await session.commit()
    await message.answer("⏸️ Publicação pausada. Use /resume para retomar.")
    logger.info("Publicação pausada por {}", message.from_user.id if message.from_user else "?")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not _is_admin(message):
        return
    async with AsyncSessionLocal() as session:
        await repo.set_paused(session, False)
        await session.commit()
    await message.answer("▶️ Publicação retomada.")
    logger.info("Publicação retomada por {}", message.from_user.id if message.from_user else "?")


@router.message(Command("preview"))
async def cmd_preview(message: Message) -> None:
    if not _is_admin(message):
        return
    async with AsyncSessionLocal() as session:
        post = await repo.peek_next_pending_post(session)
        if post is None:
            await message.answer("Fila vazia no momento.")
            return
        status = "📋 Próximo da fila"
        reason = post.reason
        text = post.message_text
        link = post.affiliate_link

    await message.answer(
        f"{status}\n<i>{reason}</i>\n\n{text}\n\n🔗 {link}",
        disable_web_page_preview=True,
    )


# ------------------------------------------------------------------ fase 2


def _job_line(nome: str, info: dict) -> str:
    status = "✅" if info.get("ok") else "❌"
    at = info.get("at", "?")[:19].replace("T", " ")
    detail = info.get("detail", "")
    return f"{status} <b>{nome}</b>: {at} UTC — {detail}"


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    """Último ciclo de cada job + erros recentes (spec 7.6)."""
    if not _is_admin(message):
        return
    async with AsyncSessionLocal() as session:
        runs = await repo.get_job_runs(session)

    if not runs:
        await message.answer("Nenhuma execução de job registrada ainda (worker rodando?).")
        return

    linhas = ["🏥 <b>Saúde dos jobs</b>"]
    for nome, info in sorted(runs.items()):
        linhas.append(_job_line(nome, info))
    await message.answer("\n".join(linhas))


@router.message(Command("blacklist"))
async def cmd_blacklist(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    term = (command.args or "").strip().lower()
    if not term:
        await message.answer("Uso: /blacklist <termo>")
        return
    async with AsyncSessionLocal() as session:
        await repo.add_blacklist_term(session, term)
        await session.commit()
    await message.answer(f"🚫 Termo adicionado à blacklist: <code>{term}</code>")
    logger.info("Blacklist: '{}' adicionado por {}", term, message.from_user.id if message.from_user else "?")


@router.message(Command("addkeyword"))
async def cmd_addkeyword(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    term = (command.args or "").strip()
    if not term:
        await message.answer("Uso: /addkeyword <termo>")
        return
    async with AsyncSessionLocal() as session:
        await repo.add_keyword(session, term)
        await session.commit()
    await message.answer(f"🔍 Keyword adicionada (todas as lojas): <code>{term}</code>")
    logger.info("Keyword '{}' adicionada por {}", term, message.from_user.id if message.from_user else "?")


@router.message(Command("removekeyword"))
async def cmd_removekeyword(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    term = (command.args or "").strip()
    if not term:
        await message.answer("Uso: /removekeyword <termo>")
        return
    async with AsyncSessionLocal() as session:
        removed = await repo.deactivate_keyword(session, term)
        await session.commit()
    if removed:
        await message.answer(f"🗑️ Keyword removida: <code>{term}</code>")
    else:
        await message.answer(f"Keyword não encontrada (ou já inativa): <code>{term}</code>")
