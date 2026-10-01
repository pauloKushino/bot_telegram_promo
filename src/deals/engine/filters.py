"""Filtros de qualidade (spec 7.3 item 1).

Função pura sobre dados do produto — fácil de testar sem banco.
Limites principais vêm de env (MIN_SELLER_RATING, MIN_SALES_COUNT,
MIN_OFFICIAL_CONFIDENCE). Faixas de preço plausíveis por tipo ficam aqui
como constante ajustável.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from deals.db.models import Product, ProductType

# Faixas plausíveis de preço (BRL) por tipo — barram anúncios errados/absurdos.
# Ajustar com base nos dados reais coletados (fase 4).
PRICE_RANGES: dict[ProductType, tuple[Decimal, Decimal]] = {
    ProductType.MANGA: (Decimal("10"), Decimal("200")),
    ProductType.FIGURE: (Decimal("50"), Decimal("5000")),
    ProductType.BLURAY: (Decimal("30"), Decimal("500")),
    ProductType.BOX: (Decimal("50"), Decimal("2000")),
    ProductType.OUTRO: (Decimal("10"), Decimal("3000")),
}


@dataclass
class FilterResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def apply_filters(
    product: Product,
    price: Decimal,
    blacklist_terms: list[str],
    *,
    min_seller_rating: float,
    min_sales_count: int,
    min_official_confidence: float,
) -> FilterResult:
    """Aplica todos os filtros. Retorna ok=False com as razões da rejeição."""
    reasons: list[str] = []

    if product.is_anime_merch is not True:
        reasons.append("não classificado como merch de anime")

    confidence = float(product.official_confidence or 0)
    if confidence < min_official_confidence:
        reasons.append(f"confiança de oficial {confidence:.2f} < {min_official_confidence}")

    if product.seller_rating is not None and float(product.seller_rating) < min_seller_rating:
        reasons.append(f"nota do vendedor {product.seller_rating} < {min_seller_rating}")

    if product.sales_count is not None and product.sales_count < min_sales_count:
        reasons.append(f"vendas {product.sales_count} < {min_sales_count}")

    title_lower = product.title.lower()
    for term in blacklist_terms:
        if term.lower() in title_lower:
            reasons.append(f"título contém termo da blacklist: '{term}'")

    if product.product_type in PRICE_RANGES:
        low, high = PRICE_RANGES[product.product_type]  # type: ignore[index]
        if not (low <= price <= high):
            reasons.append(
                f"preço R$ {price} fora da faixa plausível [{low}, {high}] p/ {product.product_type}"
            )

    return FilterResult(ok=not reasons, reasons=reasons)
