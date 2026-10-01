from datetime import UTC, datetime, timedelta
from decimal import Decimal

from deals.engine.deals import evaluate_deal, format_brl, has_enough_history, is_repost_blocked
from tests.fakes import make_history

NOW = datetime.now(UTC)


def test_format_brl():
    assert format_brl(Decimal("39.90")) == "R$ 39,90"
    assert format_brl(Decimal("1234.50")) == "R$ 1.234,50"
    assert format_brl(Decimal("0.99")) == "R$ 0,99"


def test_historico_estavel_nao_e_oferta():
    # preço estável NÃO é promoção (senão todo produto monitorado vira "oferta")
    history = make_history(("50.00", 40), ("50.00", 30), ("50.00", 20), ("50.00", 10), ("50.00", 0), now=NOW)
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert not verdict.is_deal


def test_queda_abaixo_do_minimo_anterior_e_oferta():
    history = make_history(("55.00", 40), ("49.90", 20), ("45.00", 10), ("39.90", 0), now=NOW)
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert verdict.is_deal
    assert "menor preço em 60 dias" in verdict.reason
    assert "R$ 39,90" in verdict.reason
    assert verdict.reference_price is not None


def test_igual_ao_minimo_anterior_nao_e_oferta():
    # já esteve a 39.90 antes: sem queda nova, não é oferta
    history = make_history(("55.00", 40), ("39.90", 15), ("45.00", 5), ("39.90", 0), now=NOW)
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert not verdict.is_deal


def test_pico_falso_seguido_de_retorno_nao_e_oferta():
    # Loja sobe o preço por 2 dias e depois "baixa" para o normal: golpe clássico.
    # Histórico denso como na operação real (coleta a cada 20-30 min): a mediana
    # de 30d fica em R$ 50, o pico a R$ 90 não a envenena, e R$ 50 não é menor
    # que o mínimo anterior (também R$ 50) => não é oferta.
    history = make_history(
        ("50.00", 40), ("50.00", 33), ("50.00", 30), ("50.00", 27), ("50.00", 24),
        ("50.00", 21), ("50.00", 18), ("50.00", 15), ("50.00", 12), ("50.00", 9),
        ("50.00", 6), ("50.00", 3),
        ("90.00", 2), ("90.00", 1),  # pico falso curto
        ("50.00", 0),  # volta ao normal
        now=NOW,
    )
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert not verdict.is_deal


def test_queda_vs_mediana_30d_e_oferta():
    # sem bater o mínimo de 60d (já chegou a 40 antes), mas caiu >15% vs mediana
    history = make_history(
        ("40.00", 50), ("60.00", 20), ("60.00", 15), ("60.00", 10), ("60.00", 5), ("49.90", 0), now=NOW
    )
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert verdict.is_deal
    assert "queda de" in verdict.reason
    assert "mediana de 30 dias" in verdict.reason


def test_queda_menor_que_o_limite_nao_e_oferta():
    # R$ 56 não é novo mínimo (já esteve a 55) e a queda vs mediana 30d é ~1%
    history = make_history(
        ("60.00", 40), ("55.00", 20), ("58.00", 10), ("56.00", 0), now=NOW
    )
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert not verdict.is_deal


def test_historico_insuficiente_nunca_afirma_desconto():
    history = make_history(("80.00", 3), ("40.00", 0), now=NOW)  # só 3 dias
    verdict = evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15)
    assert not verdict.is_deal
    assert "histórico insuficiente" in verdict.reason


def test_historico_vazio_ou_ponto_unico():
    assert not evaluate_deal([], now=NOW, min_history_days=7, min_drop_pct=0.15).is_deal
    history = make_history(("40.00", 0), now=NOW)
    assert not evaluate_deal(history, now=NOW, min_history_days=7, min_drop_pct=0.15).is_deal


def test_has_enough_history():
    assert has_enough_history(make_history(("50.00", 10), ("50.00", 0), now=NOW), NOW, 7)
    assert not has_enough_history(make_history(("50.00", 6), ("50.00", 0), now=NOW), NOW, 7)
    assert not has_enough_history([], NOW, 7)


# -------------------------------------------------------------- anti-repetição


def test_repost_dentro_de_72h_e_bloqueado():
    posted = NOW - timedelta(hours=24)
    assert is_repost_blocked(Decimal("49.90"), posted, Decimal("49.90"), now=NOW)
    assert is_repost_blocked(Decimal("49.90"), posted, Decimal("49.00"), now=NOW)  # queda < 10%


def test_repost_liberado_com_queda_de_10pct():
    posted = NOW - timedelta(hours=24)
    assert not is_repost_blocked(Decimal("49.90"), posted, Decimal("44.91"), now=NOW)  # -10% exato
    assert not is_repost_blocked(Decimal("49.90"), posted, Decimal("40.00"), now=NOW)


def test_repost_liberado_apos_72h():
    posted = NOW - timedelta(hours=73)
    assert not is_repost_blocked(Decimal("49.90"), posted, Decimal("49.90"), now=NOW)


def test_sem_post_anterior_nunca_bloqueia():
    assert not is_repost_blocked(None, None, Decimal("49.90"), now=NOW)
