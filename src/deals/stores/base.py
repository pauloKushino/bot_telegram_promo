from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RawOffer(BaseModel):
    """Oferta normalizada retornada por qualquer adapter de loja."""

    store: str
    external_id: str
    title: str
    url: str
    image_url: str | None = None
    price: Decimal
    seller_name: str | None = None
    seller_rating: float | None = None
    sales_count: int | None = None
    commission_rate: float | None = None


@runtime_checkable
class StoreAdapter(Protocol):
    """Contrato de um adapter de loja.

    Regra: adapters NUNCA propagam exceção de rede/API — logam e retornam
    lista vazia (ou fallback) para o ciclo de coleta continuar.
    """

    name: str

    async def search(self, keyword: str, limit: int) -> list[RawOffer]: ...
    async def build_affiliate_link(self, offer: RawOffer, sub_id: str) -> str: ...
