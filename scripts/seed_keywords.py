"""Seed manual: keywords e termos de blacklist iniciais.

Uso: uv run python scripts/seed_keywords.py
Idempotente: não duplica termos já existentes.
"""

import asyncio

from sqlalchemy import select

from deals.db.base import AsyncSessionLocal
from deals.db.models import BlacklistTerm, Keyword

KEYWORDS = [
    "mangá",
    "mangá one piece",
    "mangá jujutsu kaisen",
    "figure anime",
    "action figure",
    "box mangá",
]

# Termos da spec (seção 6, tabela blacklist_terms)
BLACKLIST = ["réplica", "similar", "bootleg", "não oficial", "pirata", "genérico", "china"]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        added_kw = 0
        for term in KEYWORDS:
            exists = await session.scalar(select(Keyword.id).where(Keyword.term == term))
            if not exists:
                session.add(Keyword(term=term, store=None, active=True))
                added_kw += 1

        added_bl = 0
        for term in BLACKLIST:
            exists = await session.scalar(select(BlacklistTerm.id).where(BlacklistTerm.term == term))
            if not exists:
                session.add(BlacklistTerm(term=term, active=True))
                added_bl += 1

        await session.commit()
    print(f"Seed: +{added_kw} keywords, +{added_bl} termos de blacklist")


if __name__ == "__main__":
    asyncio.run(main())
