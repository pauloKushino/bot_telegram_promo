"""Introspecção GraphQL da API Shopee: schema de conversionReport.

Uso: uv run python scripts/introspect_shopee.py
"""

import asyncio
import json

from deals.stores.shopee import ShopeeAdapter

PROBE = """
query {
  conversionField: __type(name: "Query") {
    fields {
      name
      type { kind name ofType { kind name ofType { kind name } } }
      args { name type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
  conversionReport: __type(name: "ConversionReport") {
    fields { name type { kind name ofType { kind name ofType { kind name } } } }
  }
}
"""

PROBE_ORDER = """
query ($nome: String!) {
  __type(name: $nome) { fields { name type { kind name ofType { kind name } } } }
}
"""


def unwrap(t: dict) -> dict:
    while t.get("kind") in ("NON_NULL", "LIST"):
        t = t.get("ofType") or {}
    return t


def type_name(t: dict) -> str:
    inner = unwrap(t)
    return inner.get("name") or inner.get("kind") or "?"


async def main() -> None:
    adapter = ShopeeAdapter.try_create()
    data = await adapter._graphql(PROBE, {})
    if not data:
        print("introspecção não retornou dados")
        return

    for f in (data.get("conversionField") or {}).get("fields", []):
        if "conversion" in f["name"].lower():
            args = ", ".join(f"{a['name']}: {type_name(a['type'])}" for a in f.get("args", []))
            print(f"Query.{f['name']}({args})")

    report = data.get("conversionReport")
    if not report:
        print("\nTipo ConversionReport não exposto; resposta bruta:")
        print(json.dumps(data, ensure_ascii=False, indent=1)[:1500])
        return

    print("\nCampos de ConversionReport:")
    for f in report["fields"]:
        print(f"  {f['name']}: {type_name(f['type'])}")

    orders_field = next((f for f in report["fields"] if f["name"] == "orders"), None)
    if orders_field:
        order_type = type_name(orders_field["type"])
        print(f"\nIntrospecção do tipo '{order_type}' (orders):")
        inner = await adapter._graphql(PROBE_ORDER, {"nome": order_type})
        for f in ((inner or {}).get("__type") or {}).get("fields") or []:
            print(f"  {f['name']}: {type_name(f['type'])}")

    # PageInfo: tem scrollId para paginação?
    pi = await adapter._graphql(
        'query { __type(name: "PageInfo") { fields { name type { kind name } } } }', {}
    )
    print("\nCampos de PageInfo:")
    for f in ((pi or {}).get("__type") or {}).get("fields") or []:
        print(f"  {f['name']}: {type_name(f['type'])}")

    # Tipo de retorno de Query.conversionReport (para saber nodes/pageInfo)
    conv_field = next(
        (f for f in (data.get("conversionField") or {}).get("fields", []) if f["name"] == "conversionReport"),
        None,
    )
    if conv_field:
        print(f"\nTipo de retorno de Query.conversionReport: {type_name(conv_field['type'])}")
        inner = await adapter._graphql(PROBE_ORDER, {"nome": type_name(conv_field["type"])})
        for f in ((inner or {}).get("__type") or {}).get("fields") or []:
            print(f"  {f['name']}: {type_name(f['type'])}")


if __name__ == "__main__":
    asyncio.run(main())
