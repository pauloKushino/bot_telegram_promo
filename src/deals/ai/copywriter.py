"""Copywriter: gera a legenda do post com IA (spec 7.4).

Regras duras:
- Usar APENAS os dados fornecidos. Proibido inventar preço, desconto, frete,
  estoque ou urgência.
- Só mencionar "menor preço em X dias" se estiver no `reason`.
- Preço citado no texto deve ser IDÊNTICO ao dos dados: validação pós-geração;
  se não bater, regera uma vez; se falhar de novo, o post é descartado
  (CopywriterError) — nunca publicar legenda com preço divergente.
"""

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger

from deals.ai.client import LLMClient
from deals.engine.deals import format_brl

DISCLOSURE_LINE = "Link de afiliado: posso receber comissão sem custo extra para você."
CAPTION_MAX_LEN = 1000  # margem abaixo do limite de 1024 do Telegram
MAX_REGENERATIONS = 1


class CopywriterError(Exception):
    """Legenda inválida mesmo após regeneração — post deve ser descartado."""


@dataclass(frozen=True)
class CopyContext:
    """Somente dados VERIFICADOS do produto/oferta."""

    title: str
    franchise: str | None
    product_type: str | None
    volume: str | None
    publisher: str | None
    store: str
    price: Decimal
    reason: str  # justificativa objetiva do deal engine (ou "novo no radar")
    is_new_on_radar: bool  # True => proibido alegar qualquer desconto


SYSTEM_PROMPT = (
    "Você escreve legendas curtas em PT-BR para um canal Telegram de promoções "
    "de anime. Tom direto e animado, sem exagero. Responda SOMENTE com o texto "
    "da legenda, sem aspas e sem explicações."
)


def build_user_prompt(ctx: CopyContext) -> str:
    price_fmt = format_brl(ctx.price)
    partes = [
        f"Título do anúncio: {ctx.title}",
        f"Loja: {ctx.store}",
        f"Preço ATUAL (use EXATAMENTE este texto quando citar o preço): {price_fmt}",
        f"Justificativa da oferta: {ctx.reason}",
    ]
    if ctx.franchise:
        partes.append(f"Franquia: {ctx.franchise}")
    if ctx.product_type:
        partes.append(f"Tipo: {ctx.product_type}")
    if ctx.volume:
        partes.append(f"Volume: {ctx.volume}")
    if ctx.publisher:
        partes.append(f"Editora/Fabricante: {ctx.publisher}")
    dados = "\n".join(partes)

    regra_desconto = (
        'Este produto é "novo no radar": NÃO afirme desconto, menor preço ou economia — '
        "apresente como novidade monitorada."
        if ctx.is_new_on_radar
        else 'Só afirme "menor preço em X dias" ou a queda percentual se estiver na justificativa acima.'
    )

    return f"""Escreva a legenda de um post de oferta com os dados abaixo.

REGRAS OBRIGATÓRIAS:
- Use APENAS os dados fornecidos. Não invente preço, desconto, frete grátis, estoque ou urgência.
- Ao citar o preço, escreva exatamente: {price_fmt}
- {regra_desconto}
- Não use o texto do botão de link (ele é adicionado separadamente).
- Termine a legenda com esta linha fixa, exatamente como está: {DISCLOSURE_LINE}
- Máximo de {CAPTION_MAX_LEN} caracteres. Varie a estrutura entre posts; 1 ou 2 emojis no máximo.

DADOS:
{dados}"""


def validate_caption(caption: str, expected_price: Decimal) -> list[str]:
    """Retorna a lista de problemas da legenda (vazia = legenda válida)."""
    problems: list[str] = []
    if format_brl(expected_price) not in caption:
        problems.append(f"preço {format_brl(expected_price)} não encontrado no texto")
    if len(caption) > CAPTION_MAX_LEN:
        problems.append(f"legenda com {len(caption)} chars > {CAPTION_MAX_LEN}")
    return problems


def ensure_disclosure(caption: str) -> str:
    """Garante a linha fixa de divulgação de afiliado no fim (conformidade)."""
    if DISCLOSURE_LINE in caption:
        return caption
    return caption.rstrip() + f"\n\n{DISCLOSURE_LINE}"


async def generate_caption(llm: LLMClient, ctx: CopyContext) -> str:
    """Gera e valida a legenda. Levanta CopywriterError se inválida 2 vezes."""
    prompt = build_user_prompt(ctx)
    last_problems: list[str] = []

    for attempt in range(MAX_REGENERATIONS + 1):  # 1 geração + MAX_REGENERATIONS regenerações
        text = await llm.chat(SYSTEM_PROMPT, prompt, max_tokens=700, temperature=0.7)
        caption = ensure_disclosure(text.strip())
        problems = validate_caption(caption, ctx.price)
        if not problems:
            return caption
        last_problems = problems
        logger.warning("Legenda inválida (tentativa {}): {}", attempt + 1, problems)

    raise CopywriterError(f"legenda rejeitada após regeneração: {last_problems}")
