import json
from decimal import Decimal

import pytest

from deals.ai.classifier import (
    ClassifierError,
    ProductInput,
    classify_products,
    parse_classifications,
)
from deals.ai.copywriter import (
    DISCLOSURE_LINE,
    CopyContext,
    CopywriterError,
    ensure_disclosure,
    generate_caption,
)
from deals.engine.deals import format_brl
from tests.fakes import FakeLLM

PRODUCT = ProductInput(
    id=7,
    title="Mangá One Piece Vol. 105 - Panini",
    seller_name="Loja Exemplo",
    price=Decimal("39.90"),
    store="shopee",
)

VALID_JSON = json.dumps(
    [
        {
            "is_anime_merch": True,
            "franchise": "One Piece",
            "product_type": "manga",
            "volume": "105",
            "publisher": "Panini",
            "official_confidence": 0.92,
            "reason": "título com editora e volume",
        }
    ]
)


async def test_classificacao_valida():
    llm = FakeLLM([VALID_JSON])
    results = await classify_products(llm, [PRODUCT])
    cls = results[7]
    assert cls.is_anime_merch is True
    assert cls.product_type == "manga"
    assert cls.official_confidence == pytest.approx(0.92)


async def test_json_com_markdown_ao_redor_e_tolerado():
    llm = FakeLLM(["Aqui está o resultado:\n```json\n" + VALID_JSON + "\n```"])
    results = await classify_products(llm, [PRODUCT])
    assert results[7].product_type == "manga"


async def test_json_invalido_tenta_de_novo():
    llm = FakeLLM(["não é json", VALID_JSON])
    results = await classify_products(llm, [PRODUCT])
    assert len(llm.calls) == 2
    assert results[7].franchise == "One Piece"


async def test_json_sempre_invalido_levanta_erro():
    llm = FakeLLM(["lixo", "lixo de novo", "mais lixo"])
    with pytest.raises(ClassifierError):
        await classify_products(llm, [PRODUCT])


async def test_quantidade_errada_de_resultados_falha():
    outro = ProductInput(id=8, title="Outro", seller_name=None, price=Decimal("10"), store="shopee")
    llm = FakeLLM([VALID_JSON, VALID_JSON, VALID_JSON])  # sempre 1 resultado p/ 2 produtos
    with pytest.raises(ClassifierError):
        await classify_products(llm, [PRODUCT, outro], batch_size=2)


def test_parse_rejeita_tipo_desconhecido_caindo_para_outro():
    text = json.dumps(
        [   
            {
                "is_anime_merch": True,
                "product_type": "action-figure-custom",
                "official_confidence": 0.8,
            }
        ]
    )
    parsed = parse_classifications(text, 1)
    assert parsed[0].product_type == "outro"


# -------------------------------------------------------------- copywriter


def _ctx(**overrides) -> CopyContext:
    defaults = dict(
        title="Mangá One Piece Vol. 105 - Panini",
        franchise="One Piece",
        product_type="manga",
        volume="105",
        publisher="Panini",
        store="shopee",
        price=Decimal("39.90"),
        reason="menor preço em 60 dias: R$ 39,90; mediana 30d: R$ 49,90",
        is_new_on_radar=False,
    )
    defaults.update(overrides)
    return CopyContext(**defaults)


async def test_copywriter_legenda_valida():
    caption = "Mangá One Piece vol. 105 por R$ 39,90! 🔥\n\n" + DISCLOSURE_LINE
    llm = FakeLLM([caption])
    result = await generate_caption(llm, _ctx())
    assert "R$ 39,90" in result
    assert result.endswith(DISCLOSURE_LINE)


async def test_copywriter_regenera_quando_preco_diverge():
    errada = "Oferta por R$ 29,90!\n\n" + DISCLOSURE_LINE  # preço inventado pela IA
    certa = "Oferta por R$ 39,90!\n\n" + DISCLOSURE_LINE
    llm = FakeLLM([errada, certa])
    result = await generate_caption(llm, _ctx())
    assert len(llm.calls) == 2
    assert "R$ 39,90" in result


async def test_copywriter_descarta_se_preco_sempre_errado():
    llm = FakeLLM(["Por R$ 29,90!", "Por R$ 35,00!"])
    with pytest.raises(CopywriterError):
        await generate_caption(llm, _ctx())


def test_ensure_disclosure_adiciona_linha_quando_falta():
    assert ensure_disclosure("texto sem linha").endswith(DISCLOSURE_LINE)
    com_linha = "texto\n\n" + DISCLOSURE_LINE
    assert ensure_disclosure(com_linha) == com_linha


async def test_prompt_radar_proibe_desconto():
    ctx = _ctx(is_new_on_radar=True, reason="novo no radar")
    caption = f"Novo no radar por {format_brl(ctx.price)}\n\n{DISCLOSURE_LINE}"
    llm = FakeLLM([caption])
    result = await generate_caption(llm, ctx)
    assert "NÃO afirme desconto" in llm.calls[0]["user"]
    assert result.endswith(DISCLOSURE_LINE)
