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

- **Loja**: Shopee Afiliados (GraphQL oficial, assinatura SHA256, validada contra a API real).
  Mercado Livre e Amazon: fase 4.
- **IA**: NVIDIA NIM (`https://integrate.api.nvidia.com/v1`, API compatível com OpenAI) via SDK `openai`. Modelo configurável por `AI_MODEL`. Usada para classificar produtos (é merch de anime oficial?) e escrever legendas.
- **Regra de ouro**: nenhum post afirma "menor preço" ou "desconto" sem prova no histórico de preço próprio (mínimo de 60 dias ou queda >=15% vs mediana de 30 dias). Sem histórico: post "novo no radar", sem alegação de economia e com limite baixo por dia.
- **Métricas (fase 2)**: importação diária de conversões da Shopee (20:50, janela de 7 dias) e
  relatório diário ao admin por DM (21:00): posts do dia, produtos novos, vendas, comissão e top 3 posts.

## Público (fase 3a — qualquer usuário no privado com o bot)

| Comando/interação | Ação |
|---|---|
| `/start` | cadastra o usuário e abre o teclado de franquias |
| Botão de franquia | segue/deixa de seguir (grátis: até `FREE_MAX_FOLLOWS` = 3) |
| 🔔 "Acompanhar este item" (posts do canal) | cria alerta: avisa por DM se o preço cair abaixo do postado (grátis: `FREE_MAX_ALERTS` = 1 ativo) |
| Botões -5%/-10%/-20% na DM de confirmação | ajusta o alvo do alerta |
| `/parar` | para de receber DMs (reversible com /start) |
| `/apagar_meus_dados` | apaga usuário, follows, alertas e logs (privacidade) |
| `/canal` | bot manda o link de entrada do canal (invite cacheado p/ canal privado) |

Cada post do canal leva 3 botões: **Ver oferta** (afiliado), **🔔 Acompanhar este item**
(alerta one-shot) e **📣 Receber alertas no privado** (deep link `t.me/<bot>?start=canal` —
abre a DM do bot sem precisar de /start prévio e grava a origem `users.source` para sabermos
quantos cadastros vêm do canal).

DMs respeitam limite diário por usuário (`MAX_DM_PER_USER_DAY` = 10), carregam `sub_id`
por usuário nos links (rastreio de conversão por assinante) e quem bloqueia o bot é
marcado e silenciado automaticamente.

## Comandos do bot (somente admins em `ADMIN_TELEGRAM_IDS`)

| Comando | Ação |
|---|---|
| `/stats` | posts hoje/semana, fila pendente, produtos monitorados, estado da publicação |
| `/pause` / `/resume` | liga/desliga a publicação (estado no banco, compartilhado bot↔worker) |
| `/preview` | mostra o próximo post da fila sem publicar |
| `/health` | última execução de cada job do worker + erros recentes |
| `/addkeyword <termo>` / `/removekeyword <termo>` | gerencia keywords de busca |
| `/blacklist <termo>` | adiciona termo à blacklist (títulos com ele nunca são postados) |

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

## Produção (VPS)

Deploy na VPS via `docker-compose.prod.yml` + runbook completo em `deploy/README.md`
(endurecimento SSH/firewall, migração do histórico local, backup diário com cópia no PC do
operador, monitor de disponibilidade via healthchecks.io). Sem portas públicas além de SSH
até a fase 3b (webhook Stripe + Caddy).

## Fases

- **Fase 1 ✅**: MVP — coleta Shopee, histórico de preços, classifier, filtros, copywriter,
  publisher com limites, comandos `/stats` `/pause` `/resume` `/preview`. Validado com APIs reais.
- **Fase 2 ✅**: `/health`, `/blacklist`, `/addkeyword`, `/removekeyword`, importação diária de
  conversões (tabela `conversions`) e relatório diário ao admin. Anti-repetição e regra de
  "menor preço em 60 dias" com prova no histórico, cobertas por testes sintéticos.
- Fase 3a ✅: usuários, seguir franquias (limite 3), alertas de preço (limite 1), botão
  "Acompanhar este item" nos posts, DMs com sub_id por usuário, `/parar`, `/apagar_meus_dados`.
  Validado por E2E real via userbot Telethon (9/9).
- Fase 3b: domínio + Caddy + webhook Stripe em modo teste.
- Fase 3c: premium R$ 9,90/mês (+ R$ 89/ano) e canal VIP via Stripe live
  (fluxo reaproveitado do `subbotTelegram`).
- Fase 4: Mercado Livre e Amazon (se as APIs/programas estiverem liberados).

## Estado da validação real (Fase 1 concluída)

Validado com credenciais reais em 2026-10:

- ✅ `docker compose up` sobe postgres, bot e worker sem erro
- ✅ Migrações Alembic aplicam do zero (host e container)
- ✅ Coleta real da Shopee grava produtos e preços (111 produtos no primeiro ciclo)
- ✅ Classifier separa merch oficial do resto em amostra real — inclusive a ambiguidade
  "mangá" (quadrinho) vs "manga" (roupa), e rebaixa a confiança de figures suspeitas
- ✅ Post real publicado no canal com foto, legenda, botão e link de afiliado encurtado
- ✅ Limites diário/intervalo/silêncio cobertos por testes e observados ao vivo
- ✅ Falha na API da loja ou da IA não derruba o worker (observado com 503/timeout da NIM)
- ✅ 47 testes passando (fakes + integração Postgres); `ruff` limpo

## Decisões relevantes

- **Assinatura Shopee** = `SHA256(AppID + Timestamp + Payload + Secret)` puro (não HMAC) —
  validado contra a API. `subIds` do `generateShortLink` aceitam apenas alfanuméricos.
- **Conversões Shopee** (`conversionReport`): sub_id chega em `utmContent`; comissão em
  `totalCommission` (string); pedido em `orders.orderId`; args `Int64` vão como string nas
  variáveis; `pageInfo` vem null quando não há conversões (por isso a query não o pede).
- **Regra "menor preço em 60 dias"**: interpretada como "estritamente abaixo do mínimo dos 60 dias
  **anteriores**" (a observação atual não entra na janela). Na leitura literal, qualquer produto
  com preço estável seria sempre "menor preço em 60 dias" — não é promoção.
- **`bot_settings`**: tabela-chave/valor extra (não listada na spec) para o estado de pausa
  compartilhado entre os processos bot e worker.
- **IA**: NVIDIA NIM (API compatível com OpenAI). Padrão `nvidia/nemotron-3-ultra-550b-a55b`,
  escolhido por benchmark empírico contra 12 modelos do catálogo
  (`scripts/benchmark_models.py`): 5/5 no gabarito de classificação, mais rápido dos bons.
  Fallback de qualidade medida: `moonshotai/kimi-k3`.

## Testes

```powershell
uv run pytest                     # unitários (fakes, sem rede)
$env:TEST_DATABASE_URL="postgresql+asyncpg://deals:deals_dev_password@localhost:15432/deals_test"
uv run pytest                     # inclui integração com Postgres real
```

Sem testes que dependam de API real no CI (spec seção 10). Integração manual com APIs reais fica
em `scripts/`:

| Script | Para quê |
|---|---|
| `check_env.py` | confere formato das credenciais sem exibir valores |
| `validate_telegram.py` | bot válido + admin do canal + pode postar |
| `validate_shopee.py` | busca real + resposta bruta + link de afiliado |
| `validate_pipeline.py` | collect → classify → build_queue com APIs reais |
| `validate_publish.py` | publica UM post pendente de verdade no canal |
| `benchmark_models.py` | compara modelos NVIDIA NIM no gabarito do classifier |
| `tg_login.py` + `e2e_admin.py` | teste E2E real dos comandos admin via userbot Telethon |

Para o E2E (`e2e_admin.py`): crie um app em https://my.telegram.org → "API development tools",
preencha `TG_API_ID`/`TG_API_HASH`/`TG_PHONE` no `.env` e rode `tg_login.py` uma vez (código
chega no seu Telegram). O arquivo `userbot.session` dá acesso total à conta: é gitignored e pode
ser revogado em Telegram → Configurações → Dispositivos.

## Segurança

Nunca commitar `.env` nem tokens/chaves (gitignore já cobre). PostgreSQL não é exposto
publicamente quando o compose sobe em produção (remova o mapeamento de porta). Divulgação de
afiliado é fixa no fim de toda legenda gerada.
