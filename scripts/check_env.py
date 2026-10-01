"""Verifica formato das credenciais do .env SEM exibir valores.

Uso: uv run python scripts/check_env.py
"""

import re

from deals.config import settings


def check(nome: str, valor: object, padrao: str) -> bool:
    ok = bool(valor) and re.match(padrao, str(valor)) is not None
    print(f"{nome}: {'OK' if ok else 'FALTA/FORMATO ESTRANHO'} (len={len(str(valor))})")
    return ok


checks = [
    check("BOT_TOKEN", settings.BOT_TOKEN, r"^\d+:[\w-]{30,}$"),
    check("CHANNEL_ID", settings.CHANNEL_ID, r"^-100\d+$"),
    check("ADMIN_TELEGRAM_IDS", ",".join(map(str, settings.ADMIN_TELEGRAM_IDS)), r"^\d+(,\d+)*$"),
    check("NVIDIA_API_KEY", settings.NVIDIA_API_KEY, r"^nvapi-.+"),
    check("SHOPEE_APP_ID", settings.SHOPEE_APP_ID, r"^\S+$"),
    check("SHOPEE_SECRET", settings.SHOPEE_SECRET, r"^\S{16,}$"),
]
print("AI_MODEL:", settings.AI_MODEL)
print("RESULTADO:", "tudo ok" if all(checks) else "FALTAM credenciais")
