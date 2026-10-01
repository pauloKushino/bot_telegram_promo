"""Valida a query conversionReport da Shopee (resposta bruta).

Uso: uv run python scripts/validate_conversions.py
Erros tipo "Cannot query field X" indicam o nome certo a ajustar em
CONVERSIONS_QUERY (stores/shopee.py).
"""

import asyncio
import json
import time

from deals.stores.shopee import ShopeeAdapter


async def main() -> None:
    adapter = ShopeeAdapter.try_create()
    assert adapter is not None

    end = int(time.time())
    start = end - 30 * 86400  # últimos 30 dias

    CAMPOS = "purchaseTime conversionStatus totalCommission utmContent orders { orderId orderStatus }"

    # variante A: variáveis Int64 como inteiros
    qa = (
        "query($start: Int64, $end: Int64, $limit: Int) {"
        f" conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end, limit: $limit)"
        f" {{ nodes {{ {CAMPOS} }} }} }}"
    )
    da = await adapter._graphql(qa, {"start": start, "end": end, "limit": 5})
    print("A) vars Int64 como int:", "OK" if da else "FALHOU")

    # variante B: Int64 como string
    db = await adapter._graphql(qa, {"start": str(start), "end": str(end), "limit": 5})
    print("B) vars Int64 como string:", "OK" if db else "FALHOU")

    # variante C: valores inline, sem variáveis
    qc = (
        "query {"
        f" conversionReport(purchaseTimeStart: {start}, purchaseTimeEnd: {end}, limit: 5)"
        f" {{ nodes {{ {CAMPOS} }} }} }}"
    )
    dc = await adapter._graphql(qc, {})
    print("C) inline:", "OK" if dc else "FALHOU")

    bruto = da or db or dc
    if bruto:
        print("\n=== resposta bruta (variante que funcionou) ===")
        print(json.dumps(bruto, ensure_ascii=False, indent=1)[:2000])

    print("\n=== parseado pelo adapter ===")
    converts = await adapter.fetch_conversions(start, end)
    for c in converts:
        print(f"- order={c.order_id} sub_id={c.sub_id} comissão=R$ {c.commission} status={c.status}")
    if not converts:
        print("(nenhuma conversão nos últimos 30 dias — normal se ninguém comprou ainda)")


if __name__ == "__main__":
    asyncio.run(main())
