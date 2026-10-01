"""Valida só a mutation generateShortLink (resposta bruta).

Uso: uv run python scripts/validate_shortlink.py
"""

import asyncio
import json

from deals.stores.shopee import SHORT_LINK_MUTATION, ShopeeAdapter


async def main() -> None:
    adapter = ShopeeAdapter.try_create()
    data = await adapter._graphql(
        SHORT_LINK_MUTATION,
        {"originUrl": "https://shopee.com.br/product/713665019/13087585978", "subIds": ["validacaofase1"]},
    )
    print(json.dumps(data, ensure_ascii=False, indent=1) if data else "FALHOU (None)")


if __name__ == "__main__":
    asyncio.run(main())
