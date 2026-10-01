"""Adapter da API oficial de afiliados da Shopee (GraphQL).

PONTOS A VALIDAR com credenciais reais (spec seção 7.1):
  - Formato exato do header Authorization (SHA256 Credential/Timestamp/Signature)
  - Nomes/tipos exatos dos campos da query productOfferV2
  - Assinatura e resposta da mutation generateShortLink

Tudo foi escrito a partir da documentação pública da Shopee Open Platform
Affiliate, mas nunca foi exercitado com credenciais reais neste projeto.
"""

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal, InvalidOperation

import httpx
from loguru import logger
from pydantic import ValidationError

from deals.config import settings
from deals.stores.base import RawOffer, StoreAdapter

TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 3

# Ajustar aqui se a doc oficial divergir (queries centralizadas de propósito).
SEARCH_QUERY = """
query productOfferV2($keyword: String!, $page: Int!, $limit: Int!) {
  productOfferV2(keyword: $keyword, page: $page, limit: $limit, sortType: 2) {
    nodes {
      itemId
      productName
      price
      priceMin
      priceMax
      sales
      ratingStar
      shopName
      commissionRate
      productLink
      offerLink
      imageUrl
    }
    pageInfo {
      hasNextPage
    }
  }
}
"""

SHORT_LINK_MUTATION = """
mutation generateShortLink($originUrl: String!, $subIds: [String]) {
  generateShortLink(input: {originUrl: $originUrl, subIds: $subIds}) {
    shortLink
  }
}
"""


class ShopeeAdapter:
    name = "shopee"

    def __init__(self, app_id: str, secret: str, graphql_url: str):
        self._app_id = app_id
        self._secret = secret
        self._url = graphql_url

    @classmethod
    def try_create(cls) -> StoreAdapter | None:
        """Só cria o adapter se houver credenciais; senão o ciclo roda sem ele."""
        if not settings.SHOPEE_APP_ID or not settings.SHOPEE_SECRET:
            logger.warning("Shopee sem credenciais (SHOPEE_APP_ID/SHOPEE_SECRET); adapter desativado")
            return None
        return cls(settings.SHOPEE_APP_ID, settings.SHOPEE_SECRET, settings.SHOPEE_GRAPHQL_URL)

    # ------------------------------------------------------------ internals

    def _auth_header(self, payload: str) -> str:
        """Assinatura HMAC-SHA256. VALIDAR formato na doc oficial."""
        timestamp = str(int(time.time()))
        factor = f"{self._app_id}{timestamp}{payload}"
        signature = hmac.new(self._secret.encode(), factor.encode(), hashlib.sha256).hexdigest()
        return f"SHA256 Credential={self._app_id}, Timestamp={timestamp}, Signature={signature}"

    async def _graphql(self, query: str, variables: dict) -> dict | None:
        """POST GraphQL com retry (backoff exponencial, máx. MAX_RETRIES).

        Nunca levanta exceção: retorna None em falha de rede/HTTP/API.
        """
        payload = json.dumps({"query": query, "variables": variables})
        headers = {"Authorization": self._auth_header(payload), "Content-Type": "application/json"}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                    resp = await client.post(self._url, content=payload, headers=headers)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                data = resp.json()
                if "errors" in data:
                    logger.error("Shopee GraphQL retornou erros: {}", data["errors"])
                    return None
                return data.get("data")
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "Shopee GraphQL falhou (tentativa {}/{}): {}", attempt, MAX_RETRIES, exc
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** (attempt - 1))
        logger.error("Shopee GraphQL: todas as {} tentativas falharam", MAX_RETRIES)
        return None

    # ------------------------------------------------------------ interface

    async def search(self, keyword: str, limit: int = 20) -> list[RawOffer]:
        data = await self._graphql(SEARCH_QUERY, {"keyword": keyword, "page": 1, "limit": limit})
        if not data:
            return []

        nodes = (data.get("productOfferV2") or {}).get("nodes") or []
        offers: list[RawOffer] = []
        for node in nodes:
            try:
                offers.append(
                    RawOffer(
                        store=self.name,
                        external_id=str(node["itemId"]),
                        title=node["productName"],
                        url=node["productLink"],  # URL limpa do produto; offerLink fica p/ afiliado
                        image_url=node.get("imageUrl"),
                        price=_to_decimal(node.get("price")),
                        seller_name=node.get("shopName"),
                        seller_rating=_to_float(node.get("ratingStar")),
                        sales_count=_to_int(node.get("sales")),
                        commission_rate=_to_float(node.get("commissionRate")),
                    )
                )
            except (KeyError, ValidationError, InvalidOperation) as exc:
                logger.warning("Oferta Shopee descartada (formato inesperado): {}", exc)
        logger.info("Shopee '{}': {} ofertas válidas de {} retornadas", keyword, len(offers), len(nodes))
        return offers

    async def build_affiliate_link(self, offer: RawOffer, sub_id: str) -> str:
        """Gera link curto com sub_id por post p/ rastrear cliques/vendas.

        Fallback: URL limpa do produto se a mutation falhar (nunca quebra o post).
        """
        data = await self._graphql(SHORT_LINK_MUTATION, {"originUrl": offer.url, "subIds": [sub_id]})
        short = ((data or {}).get("generateShortLink") or {}).get("shortLink")
        if short:
            return short
        logger.warning(
            "generateShortLink falhou para '{}'; usando URL do produto sem sub_id", offer.title[:60]
        )
        return offer.url


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None
