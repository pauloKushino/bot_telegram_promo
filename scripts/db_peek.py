"""Peek rápido no banco (debug). Uso: uv run python scripts/db_peek.py"""

import asyncio

from sqlalchemy import text

from deals.db.base import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as s:
        for t in ["users", "follows", "price_alerts", "dm_log"]:
            n = (await s.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
            print(f"{t}: {n}")
        print()
        rows = (await s.execute(text("SELECT id, tg_id, source, is_active, dm_blocked FROM users"))).all()
        print("users:", rows)
        rows = (await s.execute(text("SELECT id, user_id, franchise, active FROM follows"))).all()
        print("follows:", rows)


if __name__ == "__main__":
    asyncio.run(main())
