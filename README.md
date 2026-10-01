# Anime Deals Bot

Bot de Telegram que monitora ofertas de produtos de anime (mangás, figures, Blu-rays) em lojas
brasileiras, detecta promoções **reais** usando histórico de preço próprio, gera a legenda com IA
e publica automaticamente em um canal com link de afiliado.

Especificação completa: `planejamento-bot-promocoes-anime.md` (raiz do repositório).

## Arquitetura

Dois processos compartilhando o mesmo PostgreSQL:

```
                ┌────────────── worker ──────────────┐
Lojas (API)  →  │ collect → classify(IA) → deal engine │  a cada N min
                │        → copywriter(IA) → fila       │
                │ publish (limites diário/intervalo)   │──→ Canal Telegram
                └──────────────────┬───────────────────┘
                                   │ PostgreSQL 16
                ┌──────────────────┴───────────────────┐
                │ bot (polling): comandos admin        │
                │ /stats /pause /resume /preview       │
                └──────────────────────────────────────┘
```

- **Loja (fase 1)**: Shopee Afiliados (GraphQL oficial, assinatura HMAC-SHA256). Mercado Livre e Amazon: fase 4.
- **IA**: NVIDIA NIM (`https://integrate.api.nvidia.com/v1`, API compatível com OpenAI) via SDK `openai`. Modelo configurável por `AI_MODEL`. Usada para classificar produtos (é merch de anime oficial?) e escrever legendas.
- **Regra de ouro**: nenhum post afirma "menor preço" ou "desconto" sem prova no histórico de preço próprio (mínimo de 60 dias ou queda >=15% vs mediana de 30 dias). Sem histórico: post "novo no radar", sem alegação de economia e com limite baixo por dia.

## Stack

Python 3.12 · `uv` · aiogram 3 · SQLAlchemy 2 (async) + asyncpg + Alembic · httpx · APScheduler ·
pydantic-settings · openai (NVIDIA NIM) · loguru · ruff + pytest · Docker Compose.

## Estrutura

```
src/deals/
├── config.py            # pydantic-settings (env tipadas)
├── logging.py           # loguru
├── db/                  # engine, models, repositories
├── stores/              # base (RawOffer/StoreAdapter) + shopee
├── ai/                  # client (NVIDIA NIM), classifier, copywriter
├── engine/              # filters (qualidade/blacklist) + deals (regra de boa oferta)
├── publisher/           # queue (limites) + telegram (envio)
├── worker/              # jobs + __main__ (coleta, classifica, fila, publica)
└── bot/                 # __main__ (polling) + handlers/admin
tests/                   # fakes + testes unitários/integração
scripts/seed_keywords.py # semeia keywords e blacklist iniciais
migrations/              # Alembic
```

## Setup local

Pré-requisitos: `uv` e Docker Desktop.

```powershell
cp .env.example .env   # preencha BOT_TOKEN, NVIDIA_API_KEY, SHOPEE_APP_ID/SECRET...
```

Suba o Postgres e rode as migrações (o compose mapeia o Postgres na porta **15432** do host para
não colidir com um PostgreSQL local na 5432):

```powershell
docker compose up -d postgres
uv run alembic upgrade head
uv run python scripts/seed_keywords.py   # keywords + blacklist iniciais
uv run pytest                            # testes
uv run ruff check .                      # lint
```

Subir tudo (bot + worker + migração automática):

```powershell
docker compose up -d
docker compose logs -f worker
```

> Sem credenciais reais de Telegram/Shopee/IA, o worker roda sem cair (loga e segue) e o bot fica
> reiniciando por token inválido — configure o `.env` antes do uso real (ver "Fase 0" na spec).

## Fases

- **Fase 1 (atual)**: MVP — coleta Shopee, histórico de preços, classifier, filtros, copywriter,
  publisher com limites, comandos `/stats` `/pause` `/resume` `/preview`.
- Fase 2: regra completa do deal engine + anti-repetição já implementados e testados; faltam
  `/health`, `/blacklist`, `/addkeyword`, importação de conversões e relatório diário.
- Fase 3: plano premium (Stripe, reaproveitando o padrão do projeto `subbotTelegram`), `/start`,
  follows e alertas por DM.
- Fase 4: Mercado Livre e Amazon (se as APIs/programas estiverem liberados).

## Decisões e pendências a validar com credenciais reais

- **Shopee GraphQL**: formato do header `Authorization` (HMAC/SHA256) e nomes exatos dos campos de
  `productOfferV2`/`generateShortLink` estão centralizados no topo de `src/deals/stores/shopee.py`
  para validação rápida na primeira coleta real (spec 7.1, "a validar").
- **Regra "menor preço em 60 dias"**: interpretada como "estritamente abaixo do mínimo dos 60 dias
  **anteriores**" (a observação atual não entra na janela). Na leitura literal, qualquer produto
  com preço estável seria sempre "menor preço em 60 dias" — não é promoção.
- **`bot_settings`**: tabela-chave/valor extra (não listada na spec) para o estado de pausa
  compartilhado entre os processos bot e worker.
- **IA**: `AI_MODEL` deve ser um chat model do catálogo NVIDIA NIM (padrão `meta/llama-3.3-70b-instruct`).
  A confiabilidade da saída em JSON varia por modelo; há retry e validação Pydantic no classifier.

## Testes

```powershell
uv run pytest                     # unitários (fakes, sem rede)
$env:TEST_DATABASE_URL="postgresql+asyncpg://deals:deals_dev_password@localhost:15432/deals_test"
uv run pytest                     # inclui integração com Postgres real
```

Sem testes que dependam de API real no CI (spec seção 10). Integração manual com APIs reais fica
em `scripts/`.

## Segurança

Nunca commitar `.env` nem tokens/chaves (gitignore já cobre). PostgreSQL não é exposto
publicamente quando o compose sobe em produção (remova o mapeamento de porta). Divulgação de
afiliado é fixa no fim de toda legenda gerada.
