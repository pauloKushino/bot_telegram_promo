"""Classifier: decide se um produto é merch de anime oficial (spec 7.4).

- Processamento em lote (vários produtos por chamada) para reduzir custo.
- Resposta somente em JSON, validada com Pydantic; retry (máx. 2) se inválido.
- Na dúvida sobre ser oficial, a instrução é dar confiança baixa:
  melhor perder uma oferta do que postar pirata.
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from deals.ai.client import LLMClient
from deals.db.models import ProductType

BATCH_SIZE = 5  # lotes pequenos: menos truncamento de JSON e latência menor no tier free
MAX_JSON_RETRIES = 2
CLASSIFY_MAX_TOKENS = 4096

VALID_TYPES = {t.value for t in ProductType}


class ClassifierError(Exception):
    """Classificação falhou de forma não recuperável neste ciclo."""


@dataclass(frozen=True)
class ProductInput:
    """Dados mínimos do produto enviados à IA."""

    id: int
    title: str
    seller_name: str | None
    price: Decimal
    store: str


class Classification(BaseModel):
    """Saída da IA para UM produto (espelha o JSON da spec 7.4)."""

    is_anime_merch: bool
    franchise: str | None = None
    product_type: str = "outro"
    volume: str | None = None
    publisher: str | None = None
    official_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("product_type", mode="before")
    @classmethod
    def coerce_type(cls, value: object) -> str:
        normalized = str(value).strip().lower() if value is not None else "outro"
        return normalized if normalized in VALID_TYPES else "outro"


SYSTEM_PROMPT = (
    "Você é um classificador de anúncios de marketplace brasileiro para um canal "
    "de promoções de anime. Responda SOMENTE com JSON válido, sem markdown, "
    "sem explicações fora do JSON."
)


def build_user_prompt(products: list[ProductInput]) -> str:
    linhas = []
    for i, p in enumerate(products, start=1):
        vendedor = p.seller_name or "desconhecido"
        linhas.append(f'{i}. loja={p.store} | título="{p.title}" | vendedor="{vendedor}" | preço=R${p.price}')

    lista = "\n".join(linhas)
    return f"""Classifique cada anúncio abaixo.

Para cada um, responda:
- "is_anime_merch": true se for produto oficial de anime/mangá
  (mangá, figure, blu-ray, box, artbook, colecionável oficial)
- "franchise": franquia (ex.: "One Piece") ou null
- "product_type": um de "manga", "figure", "bluray", "box", "outro"
- "volume": número do volume como texto (ex.: "105") ou null
- "publisher": editora/fabricante (ex.: "Panini") ou null
- "official_confidence": 0 a 1 — sua confiança de que é produto OFICIAL (não pirata/bootleg/réplica)
- "reason": justificativa curta

Regras de segurança:
- Vendedor genérico, preço absurdamente baixo para figure, ou sinais de réplica
  => official_confidence BAIXO (< 0.7)
- Se não for merch de anime, use is_anime_merch=false e official_confidence=0
- Na dúvida real sobre ser oficial, dê confiança baixa: prefira perder a oferta a aprovar pirata

Anúncios:
{lista}

Responda com um array JSON com EXATAMENTE {len(products)} objetos, na MESMA ordem dos anúncios.
Exemplo de objeto:
{{"is_anime_merch": true, "franchise": "One Piece", "product_type": "manga", "volume": "105",
  "publisher": "Panini", "official_confidence": 0.92, "reason": "título com editora e volume"}}"""


def extract_json_array(text: str) -> str:
    """Extrai o primeiro array JSON do texto (tolera markdown e ruído ao redor)."""
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        raise ValueError("resposta da IA não contém um array JSON")
    return match.group(0)


def parse_classifications(text: str, expected_count: int) -> list[Classification]:
    """Valida o JSON da IA com Pydantic. Levanta ValueError se inválido."""
    raw = json.loads(extract_json_array(text))
    if not isinstance(raw, list) or len(raw) != expected_count:
        got = len(raw) if isinstance(raw, list) else type(raw).__name__
        raise ValueError(f"esperava {expected_count} classificações, veio: {got}")
    return [Classification.model_validate(item) for item in raw]


async def classify_products(
    llm: LLMClient, products: list[ProductInput], batch_size: int = BATCH_SIZE
) -> dict[int, Classification]:
    """Classifica produtos em lotes. Retorna {product_id: Classification}.

    Se um lote falhar após os retries, levanta ClassifierError para o job
    decidir (produtos não classificados ficam para o próximo ciclo).
    """
    results: dict[int, Classification] = {}
    for start in range(0, len(products), batch_size):
        batch = products[start : start + batch_size]
        results.update(await _classify_batch(llm, batch))
    return results


async def _classify_batch(llm: LLMClient, batch: list[ProductInput]) -> dict[int, Classification]:
    prompt = build_user_prompt(batch)
    last_error: Exception | None = None

    for attempt in range(1, MAX_JSON_RETRIES + 2):  # 1 tentativa + MAX_JSON_RETRIES retries
        text = await llm.chat(SYSTEM_PROMPT, prompt, max_tokens=CLASSIFY_MAX_TOKENS, temperature=0.1)
        try:
            parsed = parse_classifications(text, expected_count=len(batch))
            return {p.id: c for p, c in zip(batch, parsed, strict=True)}
        except (ValueError, KeyError) as exc:
            last_error = exc
            logger.warning(
                "JSON de classificação inválido (tentativa {}/{}): {} | início da resposta: {!r}",
                attempt, MAX_JSON_RETRIES + 1, exc, text[:200],
            )

    raise ClassifierError(f"lote de {len(batch)} produtos falhou após retries: {last_error}")
