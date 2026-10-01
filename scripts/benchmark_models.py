"""Benchmark manual de modelos NVIDIA NIM para as duas funções de IA do bot.

Roda o prompt REAL do classifier (5 produtos com gabarito conhecido) e uma
geração de legenda REAL do copywriter em cada modelo candidato, medindo:
  - se o JSON classifica/parseia corretamente (parse_classifications)
  - se os acertos batem com o gabarito esperado
  - se a legenda cita o preço exato e cabe no limite (validate_caption)
  - latência de cada chamada

Uso: uv run python scripts/benchmark_models.py [modelo1 modelo2 ...]
Sem argumentos, usa a lista padrão abaixo (somente chat models do catálogo).
"""

import asyncio
import sys
import time
from dataclasses import dataclass
from decimal import Decimal

from openai import APIError, AsyncOpenAI

from deals.ai import classifier as clf
from deals.ai import copywriter as cw
from deals.ai.classifier import ProductInput
from deals.config import settings

# Gabarito: (is_anime_merch, confidence_alta?) — o caso 2 é uma figure "similar" (réplica)
PRODUTOS = [
    ProductInput(id=1, title="Mangá One Piece Volume 105 Panini Lacrado",
                 seller_name="Loja Hq Brasil", price=Decimal("34.90"), store="shopee"),
    ProductInput(id=2, title="Action Figure Goku Super Saiyajin 25cm PVC Similar Dragon Ball",
                 seller_name="importados123", price=Decimal("29.90"), store="shopee"),
    ProductInput(id=3, title="Camiseta Básica Algodão Unissex Lisa",
                 seller_name="moda_rj", price=Decimal("39.90"), store="shopee"),
    ProductInput(id=4, title="Box Blu-ray Demon Slayer Kimetsu no Yaiba 1 Temporada",
                 seller_name="otaku_store", price=Decimal("189.90"), store="shopee"),
    ProductInput(id=5, title="Funko Pop Naruto Uzumaki Modo Six Paths 722",
                 seller_name="geekland", price=Decimal("119.90"), store="shopee"),
]
GABARITO_MERCH = [True, True, False, True, True]  # caso 2 = merch, mas confiança oficial deve ser BAIXA
CONFIANCA_BAIXA_ESPERADA = [1]  # índice do produto "similar"

COPY_CTX = cw.CopyContext(
    title="Mangá One Piece Volume 105 Panini Lacrado",
    franchise="One Piece",
    product_type="manga",
    volume="105",
    publisher="Panini",
    store="shopee",
    price=Decimal("34.90"),
    reason="menor preço em 60 dias: R$ 34,90; mediana 30d: R$ 44,90",
    is_new_on_radar=False,
)

MODELOS_PADRAO = [
    "moonshotai/kimi-k3",
    "z-ai/glm-5.3",
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "mistralai/mistral-large-2-instruct",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]

TIMEOUT = 90.0  # por chamada; modelo free saturado falha rápido


@dataclass
class Resultado:
    modelo: str
    class_ok: bool = False
    class_ms: int = 0
    acertos_gabarito: str = "-"
    confianca_replica: float | None = None
    copy_ok: bool = False
    copy_ms: int = 0
    legenda: str = ""
    erro: str = ""


async def avaliar(client: AsyncOpenAI, modelo: str) -> Resultado:
    r = Resultado(modelo=modelo)

    async def chat(
        system: str, user: str, *, max_tokens: int = 1024, temperature: float | None = None
    ) -> str:
        # timeout rígido: modelo saturado falha rápido em vez de travar o benchmark
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=modelo,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=0.1 if temperature is None else temperature,
            ),
            timeout=TIMEOUT,
        )
        return resp.choices[0].message.content or ""

    # 1) classifier com gabarito
    t0 = time.perf_counter()
    try:
        parsed = clf.parse_classifications(
            await chat(clf.SYSTEM_PROMPT, clf.build_user_prompt(PRODUTOS), max_tokens=2000),
            expected_count=len(PRODUTOS),
        )
        r.class_ms = int((time.perf_counter() - t0) * 1000)
        r.class_ok = True
        r.acertos_gabarito = "".join(
            "OK " if c.is_anime_merch == g else "X  " for c, g in zip(parsed, GABARITO_MERCH, strict=True)
        ).strip()
        r.confianca_replica = parsed[1].official_confidence
    except Exception as exc:  # noqa: BLE001 - benchmark quer registrar qualquer falha
        r.class_ms = int((time.perf_counter() - t0) * 1000)
        r.erro = f"classifier: {type(exc).__name__}: {str(exc)[:120]}"

    # 2) copywriter com validação de preço
    t0 = time.perf_counter()
    try:
        text = await chat(cw.SYSTEM_PROMPT, cw.build_user_prompt(COPY_CTX), max_tokens=700, temperature=0.7)
        r.copy_ms = int((time.perf_counter() - t0) * 1000)
        caption = cw.ensure_disclosure(text.strip())
        r.copy_ok = not cw.validate_caption(caption, COPY_CTX.price)
        r.legenda = caption
    except Exception as exc:  # noqa: BLE001
        r.copy_ms = int((time.perf_counter() - t0) * 1000)
        r.erro += f" | copywriter: {type(exc).__name__}: {str(exc)[:120]}"

    return r


async def main() -> None:
    modelos = sys.argv[1:] or MODELOS_PADRAO
    client = AsyncOpenAI(base_url=settings.AI_BASE_URL, api_key=settings.NVIDIA_API_KEY, timeout=TIMEOUT)

    print(f"Avaliando {len(modelos)} modelos (sequencial, para não estourar rate limit)...\n")
    for modelo in modelos:
        print(f"-> {modelo} ...", flush=True)
        try:
            r = await avaliar(client, modelo)
        except APIError as exc:
            r = Resultado(modelo=modelo, erro=f"API: {str(exc)[:120]}")

        # imprime na hora: resultados parciais sobrevivem a timeout do shell
        if r.erro:
            print(f"  ERRO: {r.erro}", flush=True)
        print(
            f"  classifier: json_ok={r.class_ok} ({r.class_ms}ms) "
            f"gabarito [{r.acertos_gabarito}] conf_replica={r.confianca_replica}",
            flush=True,
        )
        print(f"  copywriter: valida={r.copy_ok} ({r.copy_ms}ms)", flush=True)
        if r.legenda:
            print(f"  legenda: {r.legenda[:200]!r}", flush=True)


if __name__ == "__main__":
    # console do Windows é cp1252: legendas com emoji não podem quebrar o print
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
