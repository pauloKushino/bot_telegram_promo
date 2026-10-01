"""Wrapper mínimo da IA.

A NVIDIA NIM (build.nvidia.com) expõe API compatível com OpenAI, então o SDK
oficial `openai` funciona apenas trocando base_url/api_key. O modelo vem de
AI_MODEL (qualquer chat model do catálogo, ex.: meta/llama-3.3-70b-instruct).
"""

import asyncio
from typing import Protocol

from loguru import logger
from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from deals.config import settings

MAX_RETRIES = 3
TIMEOUT_SECONDS = 60.0


class LLMError(Exception):
    """IA indisponível após todas as tentativas."""


class LLMClient(Protocol):
    """Contrato usado por classifier/copywriter (permite FakeLLM nos testes)."""

    async def chat(
        self, system: str, user: str, *, max_tokens: int = 1024, temperature: float | None = None
    ) -> str: ...


class NvidiaLLM:
    def __init__(self, api_key: str, base_url: str, model: str, default_temperature: float):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT_SECONDS)
        self._model = model
        self._default_temperature = default_temperature

    async def chat(
        self, system: str, user: str, *, max_tokens: int = 1024, temperature: float | None = None
    ) -> str:
        temp = self._default_temperature if temperature is None else temperature
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=max_tokens,
                    temperature=temp,
                )
                content = resp.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("IA retornou resposta vazia")
                return content
            except (APIError, APITimeoutError, RateLimitError, ValueError) as exc:
                logger.warning("Chamada à IA falhou (tentativa {}/{}): {}", attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** (attempt - 1))
        raise LLMError(f"IA indisponível após {MAX_RETRIES} tentativas")


def get_llm() -> LLMClient:
    return NvidiaLLM(
        api_key=settings.NVIDIA_API_KEY,
        base_url=settings.AI_BASE_URL,
        model=settings.AI_MODEL,
        default_temperature=settings.AI_TEMPERATURE,
    )
