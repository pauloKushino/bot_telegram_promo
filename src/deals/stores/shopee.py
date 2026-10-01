"""Adapter da API oficial de afiliados da Shopee (GraphQL).

VALIDADO contra a API real em 2026-10 (scripts/validate_shopee.py):
  - Signature = SHA256 hex de (AppID + Timestamp + Payload + Secret) — puro, NÃO HMAC
  - productOfferV2 com keyword/page/limit retorna os campos mapeados abaixo
  - generateShortLink: variável subIds é [String!] e os valores devem ser
    alfanuméricos (hífen é rejeitado com "invalid sub id")
"""

import asyncio
import hashlib
import json
import time
from decimal import Decimal, InvalidOperation

import httpx
from loguru import logger
from pydantic import BaseModel, ValidationError

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
mutation generateShortLink($originUrl: String!, $subIds: [String!]) {
  generateShortLink(input: {originUrl: $originUrl, subIds: $subIds}) {
    shortLink
  }
}
"""

# Schema VALIDADO por introspecção + chamada real em 2026-10 (scripts/introspect_shopee.py,
# scripts/validate_conversions.py):
#   - sub_id do link curto aparece em utmContent
#   - comissão é totalCommission (string); pedido em orders.orderId
#   - args Int64 devem ir como STRING nas variáveis (int JSON dá "wrong type")
#   - pageInfo vem null quando não há conversões ("got null for non-null"), por isso
#     a query não o pede. Se um dia passar de 100 conversões/semana, paginar com scrollId.
CONVERSIONS_QUERY = """
query conversionReport($start: Int64!, $end: Int64!, $limit: Int!) {
  conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end, limit: $limit) {
    nodes {
      purchaseTime
      conversionStatus
      totalCommission
      utmContent
      orders {
        orderId
        orderStatus
      }
    }
  }
}
"""


class ShopeeConversion(BaseModel):
    """Conversão normalizada importada do relatório da Shopee."""

    order_id: str
    sub_id: str | None  # utmContent
    commission: Decimal | None
    status: str
    occurred_at: int  # unix timestamp (purchaseTime)


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
        """Assinatura conforme doc oficial (validada contra a API em 2026-10):

        Signature = SHA256 hexdigest de (AppID + Timestamp + Payload + Secret)
        — SHA256 puro sobre a concatenação (NÃO é HMAC), com o Payload sendo
        exatamente a string do corpo JSON enviada na requisição.
        """
        timestamp = str(int(time.time()))
        factor = f"{self._app_id}{timestamp}{payload}{self._secret}"
        signature = hashlib.sha256(factor.encode()).hexdigest()
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

    async def fetch_conversions(self, start_ts: int, end_ts: int, limit: int = 100) -> list[ShopeeConversion]:
        """Importa o relatório de conversões (spec 7.7). Nunca levanta exceção."""
        # Int64 nesta API deve vir como STRING nas variáveis ("wrong type" caso
        # contrário); scrollId omitido se None (null explícito também falha).
        # Ambos validados contra a API em 2026-10.
        variables: dict = {"start": str(start_ts), "end": str(end_ts), "limit": limit}
        data = await self._graphql(CONVERSIONS_QUERY, variables)
        if not data:
            return []

        report = data.get("conversionReport") or {}
        conversions: list[ShopeeConversion] = []
        for node in report.get("nodes") or []:
            try:
                order = node.get("orders") or {}
                commission = _to_float(node.get("totalCommission"))
                conversions.append(
                    ShopeeConversion(
                        order_id=str(order.get("orderId", "")),
                        sub_id=node.get("utmContent") or None,
                        commission=Decimal(str(commission)) if commission is not None else None,
                        status=str(node.get("conversionStatus", "unknown")).lower(),
                        occurred_at=int(node["purchaseTime"]),
                    )
                )
            except (KeyError, ValidationError, ValueError, TypeError) as exc:
                logger.warning("Conversão Shopee descartada (formato inesperado): {}", exc)
        logger.info("Conversões Shopee: {} importadas", len(conversions))
        return conversions

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
