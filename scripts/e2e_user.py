"""Teste E2E REAL dos fluxos públicos (fase 3a), via userbot (Telethon).

Fluxo: /start → teclado de franquias → seguir Jujutsu (clique real no botão) →
clicar "🔔 Acompanhar este item" num post do canal → DM de confirmação →
ajustar alvo (botão %) → /parar → /apagar_meus_dados (limpeza).

Uso: uv run python scripts/e2e_user.py
Exit code 0 = todos os checks passaram.
"""

import asyncio
import sys

from telethon import TelegramClient

from deals.config import settings
from deals.db.base import AsyncSessionLocal

BOT_USERNAME = "anipromo_bot"
CHANNEL_ID = settings.CHANNEL_ID

resultados: list[tuple[str, bool, str]] = []


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    resultados.append((nome, ok, detalhe))
    print(f"  {'PASS' if ok else 'FAIL'}  {nome}" + (f"  ({detalhe})" if detalhe else ""))


async def wait_dm(client: TelegramClient, since_id: int, timeout_s: int = 25) -> str:
    """Aguarda a próxima DM do bot com id > since_id e retorna o texto."""
    for _ in range(timeout_s):
        msgs = await client.get_messages(BOT_USERNAME, limit=1)
        if msgs and msgs[0].id > since_id and msgs[0].sender_id != (await client.get_me()).id:
            return msgs[0].text or ""
        await asyncio.sleep(1)
    return ""


async def main() -> int:
    session_name = settings.TG_SESSION_FILE.removesuffix(".session")
    client = TelegramClient(session_name, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("Sem sessão. Rode: uv run python scripts/tg_login.py")
        return 1

    me = await client.get_me()
    print(f"Logado como {me.first_name} (id={me.id})\n")

    # estado inicial: DM mais recente (marca d'água p/ detectar novas)
    last_dm = (await client.get_messages(BOT_USERNAME, limit=1))[0]
    watermark = last_dm.id if last_dm else 0

    # 1) /start via deep link do canal + teclado (franquias + botão do canal)
    await client.send_message(BOT_USERNAME, "/start canal")
    welcome = await wait_dm(client, watermark)
    check("/start responde boas-vindas", "Bem-vindo" in welcome)
    msgs = await client.get_messages(BOT_USERNAME, limit=1)
    teclado = msgs[0].buttons if msgs else None
    check("/start tem teclado de franquias", bool(teclado))
    botao_canal = None
    if teclado:
        for row in teclado:
            for b in row:
                if b.url and "t.me/" in b.url:
                    botao_canal = b
    check("teclado tem botão de link do canal", botao_canal is not None)

    # origem do cadastro gravada ("canal" do deep link)
    from sqlalchemy import func, select

    from deals.db.models import Follow
    from deals.db.models import User as DbUser

    async with AsyncSessionLocal() as s:
        dbuser = await s.scalar(select(DbUser).where(DbUser.tg_id == me.id))
        n_follows = (
            await s.scalar(
                select(func.count(Follow.id)).where(
                    Follow.user_id == (dbuser.id if dbuser else -1), Follow.active.is_(True)
                )
            )
        ) or 0
    print(f"  debug: dbuser.source={dbuser and dbuser.source!r} follows_ativos={n_follows}")
    check("source 'canal' gravado no usuário", dbuser is not None and dbuser.source == "canal")

    # 1b) comando /canal manda o link do canal
    ultimas = await client.get_messages(BOT_USERNAME, limit=1)
    wm_canal = ultimas[0].id if ultimas else watermark
    await client.send_message(BOT_USERNAME, "/canal")
    tem_link = False
    for _ in range(25):
        novas = await client.get_messages(BOT_USERNAME, limit=3)
        alvo = next(
            (m for m in novas if m.id > wm_canal and m.sender_id != me.id), None
        )
        if alvo is not None:
            btn = alvo.buttons
            tem_link = bool(btn) and any(bool(b.url) for r in btn for b in r)
            break
        await asyncio.sleep(1)
    check("/canal responde com link", tem_link)

    # 2) seguir franquia (clica no botão com Jujutsu se houver, senão no 1º)
    alvo = None
    if teclado:
        for row in teclado:
            for btn in row:
                if "jujutsu" in btn.text.lower():
                    alvo = btn
                    break
            if alvo:
                break
        alvo = alvo or teclado[0][0]
    if alvo:
        resposta = await msgs[0].click(text=alvo.text)
        texto_resp = resposta.message if hasattr(resposta, "message") else str(resposta or "")
        t = texto_resp.lower()
        ok = ("segue" in t) or ("deixou de seguir" in t) or ("limite" in t)
        check("seguir franquia (clique)", ok, texto_resp[:80])

    # 3) clicar no botão "Acompanhar este item" do último post do canal
    posts = await client.get_messages(CHANNEL_ID, limit=10)
    post_msg = next(
        (m for m in posts if m.buttons and any("Acompanhar" in b.text for r in m.buttons for b in r)),
        None,
    )
    check("canal tem post com botão Acompanhar", post_msg is not None)
    if post_msg:
        # marca d'água atualizada ANTES do clique (evita corrida com a DM antiga)
        ultimas = await client.get_messages(BOT_USERNAME, limit=1)
        watermark_track = ultimas[0].id if ultimas else watermark
        for row in post_msg.buttons:
            for b in row:
                if "Acompanhar" in b.text:
                    track_resp = await post_msg.click(text=b.text)
        txt = track_resp.message if hasattr(track_resp, "message") else str(track_resp or "")
        check("botão Acompanhar responde", "Alerta" in txt, txt[:80])
        dm_alerta = await wait_dm(client, watermark_track)
        check("DM de confirmação do alerta chegou", "Alerta criado" in dm_alerta, dm_alerta[:60])

        # 4) ajustar alvo pelo botão de % na DM
        msgs = await client.get_messages(BOT_USERNAME, limit=1)
        if msgs and msgs[0].buttons:
            pct_btn = msgs[0].buttons[0][1]  # botão do meio (-10%)
            resp = await msgs[0].click(text=pct_btn.text)
            txt = resp.message if hasattr(resp, "message") else str(resp or "")
            check("ajuste de alvo por % responde", "Alvo ajustado" in txt or "Limite" in txt, txt[:80])

    # 5) /parar
    await client.send_message(BOT_USERNAME, "/parar")
    dm = await wait_dm(client, watermark)
    check("/parar confirma", "não te mando mais DMs" in dm)

    # 6) /apagar_meus_dados (limpeza final — remove usuário, follows e alertas de teste)
    await client.send_message(BOT_USERNAME, "/apagar_meus_dados")
    dm = await wait_dm(client, watermark)
    check("/apagar_meus_dados confirma", "apagados" in dm)

    await client.disconnect()

    falhas = [n for n, ok, _ in resultados if not ok]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} checks passaram")
    if falhas:
        print("FALHARAM:", ", ".join(falhas))
        return 1
    print("E2E usuário (fase 3a): TUDO OK")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
