"""Login do userbot Telethon para testes E2E (uma vez só).

Passos:
  1. Crie um app em https://my.telegram.org → API development tools
  2. Preencha TG_API_ID, TG_API_HASH e TG_PHONE no .env
  3. Rode: uv run python scripts/tg_login.py
     (o Telegram envia o código NO TELEGRAM, caixa "Telegram" oficial)

O arquivo de sessão (default: userbot.session) fica local e NÃO é commitado.
Ele dá acesso total à sua conta — revogue em Telegram → Configurações →
Dispositivos quando quiser invalidar.
"""

import asyncio

from telethon import TelegramClient

from deals.config import settings


async def main() -> None:
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        print("Preencha TG_API_ID e TG_API_HASH no .env (veja o docstring do script).")
        return

    # remove a extensão se o usuário pôs no .env; o Telethon adiciona .session sozinho
    session_name = settings.TG_SESSION_FILE.removesuffix(".session")
    client = TelegramClient(session_name, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Já autenticado como {me.first_name} (id={me.id}). Nada a fazer.")
        return

    # input() é aceitável aqui: script interativo de uso manual (não roda em servidor)
    phone = settings.TG_PHONE or input("Telefone com DDI (ex.: +5511999998888): ").strip()  # noqa: ASYNC250
    sent = await client.send_code_request(phone)
    print("Código enviado no seu Telegram (caixa oficial 'Telegram').")
    code = input("Digite o código: ").strip()  # noqa: ASYNC250
    try:
        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
    except Exception as exc:
        # conta com verificação em duas etapas: pede a senha
        if "password" in str(exc).lower() or "Two-steps" in str(exc):
            import getpass

            await client.sign_in(password=getpass.getpass("Senha 2FA: "))
        else:
            raise

    me = await client.get_me()
    print(f"Autenticado como {me.first_name} (id={me.id}).")
    print(f"Sessão salva em '{settings.TG_SESSION_FILE}'. NÃO compartilhe este arquivo.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
