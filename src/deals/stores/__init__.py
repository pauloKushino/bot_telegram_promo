from deals.stores.base import RawOffer, StoreAdapter
from deals.stores.shopee import ShopeeAdapter

__all__ = ["RawOffer", "ShopeeAdapter", "StoreAdapter"]


def get_enabled_adapters() -> list[StoreAdapter]:
    """Adapters com credenciais configuradas. Mercado Livre/Amazon: fase 4."""
    adapters: list[StoreAdapter] = []
    shopee = ShopeeAdapter.try_create()
    if shopee is not None:
        adapters.append(shopee)
    return adapters
