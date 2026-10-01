from decimal import Decimal

from deals.worker.jobs import render_daily_report


def test_relatorio_sem_vendas():
    texto = render_daily_report(
        posts_today=5,
        new_products_today=40,
        pending_queue=3,
        conversions_today=0,
        commission_today=Decimal("0"),
        top_posts=[],
    )
    assert "Posts publicados hoje: <b>5</b>" in texto
    assert "Produtos novos monitorados: <b>40</b>" in texto
    assert "Vendas hoje: <b>0</b>" in texto
    assert "R$ 0,00" in texto
    assert "Sem vendas atribuídas" in texto
    assert "Top posts" not in texto


def test_relatorio_com_vendas_e_top3():
    texto = render_daily_report(
        posts_today=8,
        new_products_today=12,
        pending_queue=1,
        conversions_today=3,
        commission_today=Decimal("42.50"),
        top_posts=[
            ("https://s.shopee.com.br/aaa", Decimal("20.00")),
            ("https://s.shopee.com.br/bbb", Decimal("15.50")),
            ("https://s.shopee.com.br/ccc", Decimal("7.00")),
        ],
    )
    assert "Vendas hoje: <b>3</b>" in texto
    assert "R$ 42,50" in texto
    assert "Top posts" in texto
    assert "1. R$ 20,00" in texto
    assert "3. R$ 7,00" in texto
    assert texto.count("s.shopee.com.br") == 3
