"""Regra de "boa oferta" (spec 7.3 itens 2-4) — funções puras, sem banco.

Princípio: nunca declarar "menor preço" ou "desconto" sem prova nos dados.

Decisão de interpretação da spec 7.3.2: a comparação usa o histórico ANTERIOR
à observação atual (o último ponto da lista é o preço atual). Pela leitura
literal ("price <= mínimo dos últimos 60 dias" incluindo o atual), um produto
com preço ESTÁVEL sempre seria "menor preço em 60 dias" — não é promoção.
Exigimos o preço atual estritamente abaixo do mínimo anterior.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

HISTORY_WINDOW_DAYS = 60
MEDIAN_WINDOW_DAYS = 30
REPOST_WINDOW_HOURS = 72
REPOST_MIN_DROP_PCT = 0.10


@dataclass(frozen=True)
class PricePoint:
    price: Decimal
    collected_at: datetime


@dataclass(frozen=True)
class DealVerdict:
    """Resultado da avaliação de uma oferta contra o histórico."""

    is_deal: bool
    reason: str
    reference_price: Decimal | None  # mediana 30d, quando há histórico


def format_brl(value: Decimal) -> str:
    """'R$ 1.234,56' — formato usado em posts e reasons (sempre idêntico)."""
    inteiro, _, centavos = f"{value:.2f}".partition(".")
    partes = []
    while inteiro:
        partes.append(inteiro[-3:])
        inteiro = inteiro[:-3]
    return f"R$ {'.'.join(reversed(partes))},{centavos}"


def has_enough_history(history: list[PricePoint], now: datetime, min_history_days: int) -> bool:
    """Só há histórico suficiente se a observação mais antiga é anterior ao mínimo."""
    if not history:
        return False
    oldest = min(p.collected_at for p in history)
    return oldest <= now - timedelta(days=min_history_days)


def evaluate_deal(
    history: list[PricePoint],
    *,
    now: datetime,
    min_history_days: int = 7,
    min_drop_pct: float = 0.15,
) -> DealVerdict:
    """Decide se a observação mais recente de `history` é boa oferta (spec 7.3.2).

    `history` em ordem cronológica; o ÚLTIMO ponto é o preço atual e é
    excluído da janela de comparação (ver docstring do módulo).

    - Sem histórico suficiente: is_deal=False SEMPRE (não afirmar desconto).
    - Bom: atual < mínimo dos 60 dias anteriores  OU
           atual <= mediana dos 30 dias anteriores * (1 - min_drop_pct).
    """
    if not history:
        return DealVerdict(False, "sem histórico de preço", None)

    current = history[-1].price
    previous = history[:-1]
    if not has_enough_history(previous, now, min_history_days):
        return DealVerdict(False, "histórico insuficiente para afirmar desconto", None)

    cutoff_60 = now - timedelta(days=HISTORY_WINDOW_DAYS)
    cutoff_30 = now - timedelta(days=MEDIAN_WINDOW_DAYS)
    prev_60d = [p.price for p in previous if p.collected_at >= cutoff_60]
    prev_30d = [p.price for p in previous if p.collected_at >= cutoff_30]

    min_60d = min(prev_60d) if prev_60d else None
    median_30d = Decimal(str(median(prev_30d))) if prev_30d else None
    if median_30d is None:
        median_30d = min_60d

    current_fmt = format_brl(current)
    median_fmt = format_brl(median_30d) if median_30d is not None else "-"

    if min_60d is not None and current < min_60d:
        return DealVerdict(
            is_deal=True,
            reason=f"menor preço em {HISTORY_WINDOW_DAYS} dias: {current_fmt}; mediana 30d: {median_fmt}",
            reference_price=median_30d,
        )

    if median_30d is not None and current <= median_30d * Decimal(1 - min_drop_pct):
        drop_pct = (1 - current / median_30d) * 100
        return DealVerdict(
            is_deal=True,
            reason=(
                f"queda de {drop_pct:.0f}% vs mediana de {MEDIAN_WINDOW_DAYS} dias: "
                f"{median_fmt} → {current_fmt}"
            ),
            reference_price=median_30d,
        )

    min_fmt = format_brl(min_60d) if min_60d is not None else "-"
    return DealVerdict(
        is_deal=False,
        reason=f"preço {current_fmt} sem queda relevante (mín 60d: {min_fmt}, mediana 30d: {median_fmt})",
        reference_price=median_30d,
    )


def is_repost_blocked(
    last_price_at_post: Decimal | None,
    last_posted_at: datetime | None,
    current_price: Decimal,
    *,
    now: datetime | None = None,
    window_hours: int = REPOST_WINDOW_HOURS,
    min_repost_drop_pct: float = REPOST_MIN_DROP_PCT,
) -> bool:
    """Anti-repetição (spec 7.3.3): dentro de 72h, só repost se caiu >= 10%."""
    now = now or datetime.now(UTC)
    if last_posted_at is None or last_price_at_post is None:
        return False
    if last_posted_at <= now - timedelta(hours=window_hours):
        return False
    return current_price > last_price_at_post * Decimal(1 - min_repost_drop_pct)
