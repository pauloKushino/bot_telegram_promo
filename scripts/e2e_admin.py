"""Teste E2E REAL dos comandos admin do bot, via userbot (Telethon).

Envia comandos como usuário real para @anipromo_bot e valida as respostas
com asserções. Requer sessão criada por scripts/tg_login.py.

Uso: uv run python scripts/e2e_admin.py
Exit code 0 = todos os checks passaram.
"""

import asyncio
import sys

from telethon import TelegramClient

from deals.config import settings
from deals.db import repositories as repo
from deals.db.base import AsyncSessionLocal

BOT_USERNAME = "anipromo_bot"
RESPONSE_TIMEOUT = 20  # segundos aguardando resposta do bot

resultados: list[tuple[str, bool, str]] = []


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((nome, ok, detalhe))
    print(f"  {'PASS' if ok else 'FAIL'}  {nome}" + (f"  ({detalhe})" if detalhe else ""))


async def conversa(conv, comando: str) -> str:
    """Manda comando e aguarda a resposta do bot; retorna o texto."""
    await conv.send_message(comando)
    resp = await conv.get_response()
    return resp.text or ""


async def main() -> int:
    session_name = settings.TG_SESSION_FILE.removesuffix(".session")
    client = TelegramClient(session_name, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("Sem sessão válida. Rode antes: uv run python scripts/tg_login.py")
        return 1

    me = await client.get_me()
    print(f"Logado como {me.first_name} (id={me.id}); testando contra @{BOT_USERNAME}\n")

    if me.id not in settings.ADMIN_TELEGRAM_IDS:
        print("AVISO: esta conta NÃO está em ADMIN_TELEGRAM_IDS — os comandos seriam ignorados.")
        print("       (útil para testar o caso não-admin; rode depois com a conta admin)")
        return 1

    async with client.conversation(BOT_USERNAME, timeout=RESPONSE_TIMEOUT) as conv:
        # 1) /stats responde com os campos esperados
        text = await conversa(conv, "/stats")
        check(
            "/stats responde",
            "Stats" in text and "Produtos monitorados" in text,
            text[:60].replace("\n", " "),
        )

        # 2) /pause -> resposta e estado no banco
        text = await conversa(conv, "/pause")
        check("/pause confirma", "pausada" in text.lower())
        async with AsyncSessionLocal() as s:
            check("/pause persiste no banco", await repo.is_paused(s))

        # 3) /stats reflete pausa
        text = await conversa(conv, "/stats")
        check("/stats mostra pausada", "pausada" in text)

        # 4) /preview: fila vazia ou prévia — os dois são válidos
        text = await conversa(conv, "/preview")
        check(
            "/preview responde",
            ("Próximo da fila" in text) or ("Fila vazia" in text),
            text[:50].replace("\n", " "),
        )

        # 5) /resume restaura
        text = await conversa(conv, "/resume")
        check("/resume confirma", "retomada" in text.lower())
        async with AsyncSessionLocal() as s:
            check("/resume persiste no banco", not await repo.is_paused(s))

        # 6) /stats final: de volta ao normal
        text = await conversa(conv, "/stats")
        check("/stats mostra ativa", "ativa" in text)

    await client.disconnect()

    falhas = [n for n, ok, _ in resultados if not ok]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} checks passaram")
    if falhas:
        print("FALHARAM:", ", ".join(falhas))
        return 1
    print("E2E admin: TUDO OK")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
