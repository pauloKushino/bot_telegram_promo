"""Validação manual da API Shopee Afiliados (spec 7.1, pontos "a validar").

Uso: uv run python scripts/validate_shopee.py [keyword]
Mostra status, forma da resposta bruta e as ofertas parseadas.
"""

import asyncio
import json
import sys

from deals.stores.shopee import SEARCH_QUERY, ShopeeAdapter


async def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "mangá"
    adapter = ShopeeAdapter.try_create()
    assert adapter is not None, "sem credenciais Shopee no .env"

    # 1) resposta bruta (para validar campos/assinatura na doc oficial)
    data = await adapter._graphql(SEARCH_QUERY, {"keyword": keyword, "page": 1, "limit": 5})
    if data is None:
        print("FALHOU: resposta None (ver logs). Assinatura ou query divergentes da doc.")
        return
    print("=== resposta bruta (primeiros 1200 chars) ===")
    print(json.dumps(data, ensure_ascii=False, indent=1)[:1200])

    # 2) pipeline normalizado do adapter
    offers = await adapter.search(keyword, limit=5)
    print(f"\n=== {len(offers)} ofertas parseadas ===")
    for o in offers:
        print(
            f"- [{o.external_id}] {o.title[:70]} | R$ {o.price} "
            f"| vendas={o.sales_count} | nota={o.seller_rating} | loja={o.seller_name}"
        )

    # 3) link de afiliado com sub_id
    if offers:
        link = await adapter.build_affiliate_link(offers[0], "validacao-fase1")
        print(f"\nlink de afiliado (sub_id=validacao-fase1):\n{link}")


if __name__ == "__main__":
    asyncio.run(main())
