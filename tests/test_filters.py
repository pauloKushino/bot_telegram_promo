from decimal import Decimal

from deals.db.models import ProductType
from deals.engine.filters import apply_filters
from tests.fakes import make_product

KW = dict(min_seller_rating=4.5, min_sales_count=5, min_official_confidence=0.7)


def test_produto_bom_passa():
    result = apply_filters(make_product(), Decimal("39.90"), ["réplica"], **KW)
    assert result.ok, result.reasons


def test_nao_classificado_como_anime_e_barrado():
    result = apply_filters(make_product(is_anime_merch=False), Decimal("39.90"), [], **KW)
    assert not result.ok
    assert any("não classificado" in r for r in result.reasons)


def test_sem_classificacao_nenhuma_e_barrado():
    # is_anime_merch NULL = ainda não classificado pela IA
    result = apply_filters(make_product(is_anime_merch=None), Decimal("39.90"), [], **KW)
    assert not result.ok


def test_confianca_baixa_e_barrada():
    result = apply_filters(make_product(official_confidence=Decimal("0.40")), Decimal("39.90"), [], **KW)
    assert not result.ok
    assert any("confiança" in r for r in result.reasons)


def test_vendedor_nota_baixa_e_barrado():
    result = apply_filters(make_product(seller_rating=Decimal("4.10")), Decimal("39.90"), [], **KW)
    assert not result.ok
    assert any("vendedor" in r for r in result.reasons)


def test_vendedor_sem_nota_nao_e_barrado():
    # loja que não informa nota: o filtro não se aplica
    result = apply_filters(make_product(seller_rating=None), Decimal("39.90"), [], **KW)
    assert result.ok, result.reasons


def test_poucas_vendas_e_barrado():
    result = apply_filters(make_product(sales_count=2), Decimal("39.90"), [], **KW)
    assert not result.ok
    assert any("vendas" in r for r in result.reasons)


def test_blacklist_no_titulo_e_barrada_case_insensitive():
    product = make_product(title="Figure RÉPLICA goku super sayajin")
    result = apply_filters(product, Decimal("99.90"), ["réplica"], **KW)
    assert not result.ok
    assert any("blacklist" in r for r in result.reasons)


def test_preco_fora_da_faixa_plausivel_e_barrado():
    # mangá a R$ 5 é erro de anúncio (faixa: 10 a 200)
    result = apply_filters(make_product(), Decimal("5.00"), [], **KW)
    assert not result.ok
    assert any("faixa plausível" in r for r in result.reasons)

    result_high = apply_filters(make_product(), Decimal("500.00"), [], **KW)
    assert not result_high.ok


def test_tipo_figure_tem_faixa_diferente():
    figure = make_product(product_type=ProductType.FIGURE)
    assert apply_filters(figure, Decimal("350.00"), [], **KW).ok
    assert not apply_filters(figure, Decimal("30.00"), [], **KW).ok
