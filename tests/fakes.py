"""Fakes para testar o pipeline sem rede (spec seção 10)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from deals.ai.client import LLMError
from deals.db.models import Product, ProductType, Store
from deals.engine.deals import PricePoint
from deals.stores.base import RawOffer


class FakeStoreAdapter:
    """Adapter de loja que retorna ofertas programadas e nunca fala com a rede."""

    name = "shopee"

    def __init__(self, offers: list[RawOffer] | None = None, *, fail: bool = False):
        self.offers = offers or []
        self.fail = fail
        self.search_calls: list[str] = []
        self.affiliate_calls: list[tuple[str, str]] = []

    async def search(self, keyword: str, limit: int) -> list[RawOffer]:
        self.search_calls.append(keyword)
        if self.fail:
            return []  # adapter nunca levanta exceção (contrato StoreAdapter)
        return self.offers[:limit]

    async def build_affiliate_link(self, offer: RawOffer, sub_id: str) -> str:
        self.affiliate_calls.append((offer.external_id, sub_id))
        return f"{offer.url}?af_sub={sub_id}"


class FakeLLM:
    """LLM que consome respostas programadas em fila e registra as chamadas."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    async def chat(
        self, system: str, user: str, *, max_tokens: int = 1024, temperature: float | None = None
    ) -> str:
        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "temperature": temperature}
        )
        if not self.responses:
            raise LLMError("FakeLLM sem resposta programada")
        return self.responses.pop(0)


def make_product(**overrides) -> Product:
    """Produto em memória (sem banco) com defaults que passam nos filtros."""
    defaults = dict(
        id=1,
        store=Store.SHOPEE,
        external_id="123",
        title="Mangá One Piece Vol. 105 - Panini",
        url="https://shopee.com.br/produto/123",
        image_url="https://img.exemplo.com/123.jpg",
        seller_name="Loja Exemplo",
        seller_rating=Decimal("4.80"),
        sales_count=1200,
        commission_rate=Decimal("0.08"),
        is_anime_merch=True,
        franchise="One Piece",
        product_type=ProductType.MANGA,
        volume="105",
        publisher="Panini",
        official_confidence=Decimal("0.95"),
        active=True,
    )
    defaults.update(overrides)
    return Product(**defaults)


def make_history(*points: tuple[str, int], now: datetime | None = None) -> list[PricePoint]:
    """Histórico sintético: (preço, dias_atrás) em ordem cronológica automática."""
    now = now or datetime.now(UTC)
    rows = [
        PricePoint(price=Decimal(preco), collected_at=now - timedelta(days=dias))
        for preco, dias in points
    ]
    rows.sort(key=lambda p: p.collected_at)  # evaluate_deal exige ordem cronológica
    return rows
