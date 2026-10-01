# Planejamento: Bot de Promoções de Anime (Telegram)

> **Como usar:** cole este documento inteiro como contexto inicial no Claude Code. Ele é a especificação do projeto. Implemente **uma fase por vez** (seção 9), rode os testes ao fim de cada fase e **pare para eu validar** antes de avançar.

---

## 1. Objetivo

Criar um sistema que monitora ofertas de produtos de anime (mangás, figures, Blu-rays, boxes) em lojas brasileiras, detecta promoções **reais** usando histórico de preço próprio, gera a legenda com IA e publica automaticamente em um canal de Telegram com link de afiliado.

Receita: comissão de afiliados (base) e, mais tarde, plano premium com alertas personalizados.

Prioridades, em ordem: (1) confiabilidade das ofertas (nunca postar desconto falso), (2) simplicidade de manutenção, (3) custo baixo de infraestrutura e de IA.

---

## 2. Regras de trabalho para o agente

- Trabalhe por fases. Ao terminar uma fase: rodar testes, atualizar o README, listar o que foi feito e **aguardar minha confirmação**.
- Não crie funcionalidades fora do escopo da fase atual.
- Não invente campos, endpoints ou assinaturas de API. Quando a spec disser "a validar", consulte a documentação oficial ou teste com uma chamada real e me avise se algo divergir.
- Nunca commitar segredos. Tudo em `.env` (com `.env.example` versionado).
- Commits pequenos, com mensagens claras.
- Código em Python tipado (type hints), funções pequenas, sem abstração desnecessária.
- Logs em português ou inglês, mas consistentes. Erros de API externa nunca podem derrubar o processo: tratar, logar e seguir.
- Se existir uma pasta `reference/` com outro repositório, use-a só como referência de padrões (config, sessão de banco, Alembic, Stripe). Não copie o que não for necessário.

---

## 3. Stack

| Camada | Escolha | Motivo |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema de bot e IA |
| Gerenciador de pacotes | `uv` | Rápido e simples |
| Bot Telegram | `aiogram` 3.x | Async, maduro, já conhecido |
| Banco | PostgreSQL 16 | Histórico de preços e consultas |
| ORM / migrações | SQLAlchemy 2 (async) + `asyncpg` + Alembic | Padrão do ecossistema |
| HTTP | `httpx` (async) | Chamadas às APIs das lojas |
| Agendamento | `APScheduler` (AsyncIOScheduler) no worker | Jobs periódicos sem infraestrutura extra |
| Configuração | `pydantic-settings` | Env vars tipadas e validadas |
| IA | SDK `anthropic`, modelo `claude-haiku-4-5-20251001` | Barato e rápido para classificar e escrever |
| Logs | `loguru` (ou `structlog`) | Logs legíveis |
| Qualidade | `ruff` + `pytest` + `pytest-asyncio` | Lint e testes |
| Containers | Docker + Docker Compose | Mesmo ambiente local e VPS |
| Pagamentos (fase 3) | Stripe Checkout + webhooks | Já usado no projeto anterior |

Decisões importantes:
- **Dois processos** compartilhando o mesmo PostgreSQL: `bot` (comandos e interação) e `worker` (coleta, análise e publicação).
- **Polling** no bot (sem webhook) no MVP, porque o bot só publica no canal e responde comandos do admin. Webhook/Nginx/SSL só entram na fase 3, por causa do Stripe.
- **Redis fica de fora** do MVP. Adicionar só se surgir necessidade real de cache ou FSM.

---

## 4. Arquitetura

```
        ┌──────────────────────── worker ────────────────────────┐
        │                                                          │
Lojas → │ Collector → Normalizer → Classifier(IA) → Deal Engine    │
(API)   │    │                          │               │          │
        │    └──────→ price_history ←───┘               ↓          │
        │                                        Copywriter(IA)    │
        │                                               ↓          │
        │                                          post_queue      │
        │                                               ↓          │
        │                                          Publisher ──────┼──→ Canal Telegram
        └──────────────────────────────────────────────────────────┘
                     ↑ PostgreSQL ↓
        ┌──────────────────────── bot ─────────────────────────────┐
        │ Comandos admin (/stats /pause /preview ...)               │
        └──────────────────────────────────────────────────────────┘
```

Fluxo de um ciclo do worker (a cada N minutos):
1. **Collect**: para cada keyword ativa e cada loja, buscar ofertas pela API oficial.
2. **Normalize**: converter para o formato interno (`RawOffer`), gravar/atualizar `products` e inserir linha em `price_history`.
3. **Classify**: produtos novos ou sem classificação passam pela IA (é merch de anime oficial? qual franquia/tipo/volume/editora?). O resultado é cacheado no banco.
4. **Deal Engine**: aplicar filtros de qualidade e a regra de "boa oferta" (seção 7.3).
5. **Copywrite**: gerar a legenda com IA a partir dos dados verificados.
6. **Queue/Publish**: enfileirar e publicar respeitando limites diários, intervalo mínimo e horário de silêncio.

---

## 5. Estrutura de pastas

```
anime-deals-bot/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── migrations/
├── src/
│   └── deals/
│       ├── config.py              # pydantic-settings
│       ├── logging.py
│       ├── db/
│       │   ├── base.py            # engine, sessionmaker
│       │   ├── models.py
│       │   └── repositories.py    # acesso a dados
│       ├── stores/
│       │   ├── base.py            # interface StoreAdapter + RawOffer
│       │   ├── shopee.py
│       │   └── mercadolivre.py    # fase 4 (Amazon também)
│       ├── ai/
│       │   ├── client.py          # wrapper do SDK anthropic
│       │   ├── classifier.py
│       │   └── copywriter.py
│       ├── engine/
│       │   ├── filters.py         # filtros de qualidade e blacklist
│       │   └── deals.py           # regra de boa oferta
│       ├── publisher/
│       │   ├── queue.py
│       │   └── telegram.py
│       ├── worker/
│       │   ├── __main__.py        # entrada do worker
│       │   └── jobs.py            # collect, classify, publish, reports
│       └── bot/
│           ├── __main__.py        # entrada do bot
│           └── handlers/admin.py
└── tests/
    ├── fakes.py                   # FakeStoreAdapter, FakeLLM
    ├── test_deals.py
    ├── test_filters.py
    ├── test_classifier.py
    └── test_publisher.py
```

---

## 6. Modelo de dados

Usar timestamps em UTC (`timestamptz`). Tabelas (campos principais):

**products**
- `id` (PK), `store` (enum: shopee, mercadolivre, amazon), `external_id` (texto), `title`, `url` (URL limpa do produto), `image_url`, `seller_name`, `seller_rating` (numérico, nullable), `sales_count` (int, nullable), `commission_rate` (numérico, nullable)
- Classificação da IA: `is_anime_merch` (bool, nullable), `franchise`, `product_type` (manga, figure, bluray, box, outro), `volume`, `publisher`, `official_confidence` (0 a 1), `classified_at`
- `first_seen_at`, `last_seen_at`, `active` (bool)
- Restrição única: `(store, external_id)`

**price_history**
- `id`, `product_id` (FK), `price` (numérico, em BRL), `price_min`/`price_max` (se a loja expõe faixa), `collected_at`
- Índice em `(product_id, collected_at)`

**keywords**
- `id`, `term`, `store` (nullable = todas), `active`

**blacklist_terms**
- `id`, `term` (ex.: "réplica", "similar", "bootleg", "não oficial", "pirata", "genérico", "china"), `active`

**post_queue**
- `id`, `product_id`, `price_at_post`, `reference_price` (mediana ou mínimo usado na comparação), `reason` (texto curto: "menor preço em 60 dias", "queda de X% vs mediana"), `message_text`, `affiliate_link`, `sub_id`, `status` (pending, posted, failed, skipped), `scheduled_for`, `posted_at`, `telegram_message_id`, `created_at`

**conversions** (fase 2)
- `id`, `store`, `sub_id`, `order_id`, `commission`, `status`, `occurred_at`

**users / follows / alerts / subscriptions** (fase 3, criar só nessa fase)

---

## 7. Módulos

### 7.1 Adapters de loja

Interface comum em `stores/base.py`:

```python
class RawOffer(BaseModel):
    store: str
    external_id: str
    title: str
    url: str
    image_url: str | None
    price: Decimal
    seller_name: str | None
    seller_rating: float | None
    sales_count: int | None
    commission_rate: float | None

class StoreAdapter(Protocol):
    name: str
    async def search(self, keyword: str, limit: int) -> list[RawOffer]: ...
    async def build_affiliate_link(self, offer: RawOffer, sub_id: str) -> str: ...
```

**Shopee (fase 1)**: usar a API oficial de afiliados (GraphQL). Pontos a **validar na documentação oficial antes de codar**:
- Endpoint do Brasil: `https://open-api.affiliate.shopee.com.br/graphql`
- Autenticação: assinatura HMAC/SHA256 com AppID, timestamp, payload e Secret, enviada no header `Authorization`. Confirmar o formato exato.
- Query de busca de ofertas por palavra-chave (campos como nome, preço, avaliação, vendas, comissão, link do produto e link de oferta). Confirmar nomes e tipos exatos dos campos.
- Geração de link curto com `subIds` para rastrear cada post.
- Não depender de bibliotecas não oficiais: escrever um cliente pequeno com `httpx`.

**Mercado Livre e Amazon (fase 4)**: verificar, na época, o que a API/programa permite (a Amazon exige critérios de audiência para ativar a conta de associado e migrou da PA-API para a Creators API). Só implementar se o acesso estiver liberado.

Regras para todos os adapters: timeout curto, retry com backoff exponencial (máx. 3), respeitar rate limit, nunca lançar exceção não tratada para fora do adapter.

### 7.2 Pipeline de coleta

- Job `collect` a cada 15 a 30 minutos (configurável).
- Upsert de produtos por `(store, external_id)`.
- Inserir em `price_history` apenas se o preço mudou ou se passou mais de 6 horas da última linha (evita inchar a tabela).
- Produtos não vistos por 7 dias: `active = false`.

### 7.3 Regra de "boa oferta" (Deal Engine)

Objetivo: nunca declarar "menor preço" ou "desconto" sem prova nos dados.

1. **Filtros de qualidade** (em `engine/filters.py`), todos configuráveis por env:
   - `is_anime_merch = true` e `official_confidence >= 0.7`
   - nota do vendedor >= 4.5 (se a loja informar)
   - vendas mínimas (ex.: >= 5), quando informado
   - título não contém termo da `blacklist_terms`
   - preço dentro de faixa plausível para o tipo de produto (ex.: mangá entre R$ 10 e R$ 200) para barrar erros de anúncio
2. **Comparação com histórico**:
   - Exige pelo menos `MIN_HISTORY_DAYS` (padrão 7) de histórico do produto.
   - Com histórico suficiente, é "boa oferta" se: `price <= mínimo dos últimos 60 dias` **ou** `price <= mediana dos últimos 30 dias * (1 - MIN_DROP_PCT)` (padrão 15%).
   - Sem histórico suficiente: **não** afirmar desconto. Opcionalmente postar como "novo no radar" com limite baixo por dia e sem nenhuma alegação de economia.
3. **Anti-repetição**: não postar o mesmo produto mais de uma vez em 72h, a menos que o preço caia pelo menos 10% abaixo do que foi postado.
4. Guardar em `post_queue.reason` a justificativa objetiva (ex.: "menor preço em 60 dias: R$ 39,90; mediana 30d: R$ 49,90").

### 7.4 IA (`ai/`)

Usar `claude-haiku-4-5-20251001` via SDK `anthropic` (async). Regras gerais: temperatura baixa na classificação, resposta **somente em JSON**, validação com Pydantic e retry (máx. 2) se o JSON vier inválido.

**Classifier** (entrada: título, nome do vendedor, preço, loja). Saída:
```json
{
  "is_anime_merch": true,
  "franchise": "One Piece",
  "product_type": "manga",
  "volume": "105",
  "publisher": "Panini",
  "official_confidence": 0.92,
  "reason": "título com editora e volume"
}
```
- Resultado cacheado em `products`. Reclassificar só se o título mudar.
- Se a dúvida for real sobre ser oficial, dar confiança baixa. Melhor perder uma oferta do que postar pirata.
- Processar em lote (vários produtos por chamada) para reduzir custo.

**Copywriter** (entrada: dados verificados do produto, preço atual, `reason`, preço de referência). Saída: texto curto em PT-BR para o Telegram.
- Usar **apenas** os dados fornecidos. Proibido inventar preço, desconto, frete, estoque ou urgência.
- Só mencionar "menor preço em X dias" se vier no campo `reason`.
- Tom: direto e animado, sem exagero. Variar a estrutura entre posts.
- Terminar com linha fixa de divulgação: "Link de afiliado: posso receber comissão sem custo extra para você."
- **Validação pós-geração**: o preço citado no texto deve ser idêntico ao preço dos dados. Se não bater, regerar uma vez; se falhar de novo, descartar o post.

### 7.5 Publisher

- Fila em `post_queue`; job `publish` a cada minuto verifica o que está pendente.
- Limites configuráveis: máximo de posts por dia (`MAX_POSTS_PER_DAY`, padrão 20), intervalo mínimo entre posts (`MIN_POST_INTERVAL_MIN`, padrão 10), horário de silêncio (`QUIET_HOURS`, padrão 00:00 a 07:00, fuso `America/Sao_Paulo`).
- Formato do post: foto do produto + legenda + botão inline "Ver oferta" com o link de afiliado.
- Gerar um `sub_id` único por post para rastrear cliques/vendas por post.
- Tratar erros do Telegram (flood control com `retry_after`, mensagem muito longa, foto inválida → postar sem foto).

### 7.6 Comandos do admin (bot)

Restritos por `ADMIN_TELEGRAM_IDS`. Usuário não admin recebe apenas uma mensagem curta ou nada.

- `/stats`: posts de hoje/semana, ofertas na fila, produtos monitorados
- `/pause` e `/resume`: liga e desliga a publicação
- `/preview`: mostra a próxima oferta candidata sem publicar
- `/addkeyword <termo>` e `/removekeyword <termo>`
- `/blacklist <termo>`: adiciona termo à blacklist
- `/health`: último ciclo de coleta, erros recentes

### 7.7 Métricas (fase 2)

- Job diário que importa relatório de conversões da API de afiliados e grava em `conversions`.
- Mensagem diária ao admin com: posts feitos, cliques (se disponível), vendas, comissão estimada e top 3 posts.

---

## 8. Variáveis de ambiente (`.env.example`)

```
# Telegram
BOT_TOKEN=
CHANNEL_ID=            # id do canal (ex.: -100...)
ADMIN_TELEGRAM_IDS=    # lista separada por vírgula

# Banco
DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/deals

# IA
ANTHROPIC_API_KEY=
AI_MODEL=claude-haiku-4-5-20251001

# Shopee Afiliados
SHOPEE_APP_ID=
SHOPEE_SECRET=

# Comportamento
COLLECT_INTERVAL_MIN=20
MAX_POSTS_PER_DAY=20
MIN_POST_INTERVAL_MIN=10
QUIET_HOURS=00:00-07:00
TIMEZONE=America/Sao_Paulo
MIN_HISTORY_DAYS=7
MIN_DROP_PCT=0.15
MIN_SELLER_RATING=4.5
MIN_OFFICIAL_CONFIDENCE=0.7
LOG_LEVEL=INFO
```

---

## 9. Fases e critérios de aceite

### Fase 0: preparação (manual, fora do código)
- Criar o bot no @BotFather e o canal; adicionar o bot como administrador do canal.
- Cadastrar-se na Shopee Afiliados e obter AppID/Secret da API.
- Conseguir a chave da API da Anthropic.

### Fase 1: MVP funcional
Escopo: estrutura do projeto, Docker Compose (postgres + bot + worker), modelos + migrações, adapter Shopee, coleta, histórico de preço, classifier, filtros básicos, copywriter, publisher com limites, comandos `/stats`, `/pause`, `/resume`, `/preview`.

Critérios de aceite:
- [ ] `docker compose up` sobe postgres, bot e worker sem erro.
- [ ] Migrações Alembic aplicam do zero.
- [ ] Uma coleta real da Shopee grava produtos e preços no banco.
- [ ] O classifier separa merch oficial de anime do restante em uma amostra real (mostrar 20 exemplos com o resultado).
- [ ] Um post real é publicado no canal com foto, legenda, botão e link de afiliado.
- [ ] Limites diários, intervalo mínimo e horário de silêncio funcionam (teste automatizado).
- [ ] Falha na API da loja ou da IA não derruba o worker.
- [ ] Testes com fakes (sem chamadas reais) passando; `ruff` sem erros.
- Nesta fase, enquanto não houver histórico: postar só como "novo no radar", sem alegar desconto, com limite baixo por dia.

### Fase 2: qualidade e métricas
Escopo: regra completa do Deal Engine (seção 7.3), anti-repetição, `/health`, `/blacklist`, `/addkeyword`, importação de conversões, relatório diário.

Critérios de aceite:
- [ ] Nenhum post afirma "menor preço" sem histórico que comprove (teste automatizado com histórico sintético).
- [ ] Validação de preço no texto da legenda funcionando (teste com LLM falso que erra o preço).
- [ ] Relatório diário chega ao admin com dados reais.

### Fase 3: personalização e premium
Escopo: tabelas `users`, `follows`, `alerts`, `subscriptions`; comando `/start`; seguir franquias por botões inline; alertas de preço-alvo por DM; plano premium via Stripe Checkout + webhook (reaproveitar o padrão do projeto anterior); webhook com Nginx/Caddy + SSL.

Critérios de aceite:
- [ ] Usuário segue uma franquia e recebe DM quando sai oferta dela.
- [ ] Alerta de preço-alvo dispara quando o preço cai abaixo do alvo.
- [ ] Assinatura via Stripe (modo teste) libera acesso premium e o webhook é idempotente.

### Fase 4: expansão
Escopo: Mercado Livre e Amazon (se o acesso a API/programa estiver liberado), novas keywords, ajustes finos de filtros com base nos dados reais.

---

## 10. Testes

- Usar `FakeStoreAdapter` e `FakeLLM` (em `tests/fakes.py`) para testar o pipeline inteiro sem rede.
- Cobrir com testes unitários: filtros, blacklist, regra de boa oferta (com históricos sintéticos: estável, em queda, com pico falso), anti-repetição, limites do publisher, validação do preço na legenda, parsing de JSON inválido da IA.
- Um teste de integração com PostgreSQL real (via Docker) para as queries de histórico (mínimo 60 dias, mediana 30 dias).
- Sem testes que dependam de API real no CI. Testes manuais com API real ficam em scripts separados em `scripts/`.

---

## 11. Deploy

- VPS pequena com Docker e Docker Compose.
- `restart: unless-stopped` nos serviços.
- PostgreSQL **não** exposto publicamente (somente rede interna do Compose).
- Backup diário com `pg_dump` (cron) para fora da VPS.
- Segredos só no `.env` do servidor; rotacionar o token do bot se vazar.
- Fase 3: Caddy ou Nginx com SSL para o webhook do Stripe.

---

## 12. Fora do escopo (não implementar)

- Scraping direto de páginas de lojas (usar só APIs oficiais de afiliados).
- Automação de WhatsApp por APIs não oficiais.
- Painel web, front-end, app mobile.
- Geração de imagens por IA ou uso de artes oficiais de anime nos posts (usar apenas a foto do próprio anúncio do produto).
- Qualquer texto que invente desconto, escassez ou urgência.

---

## 13. Conformidade e riscos

- **Divulgação de afiliado** obrigatória em todos os posts (linha fixa na legenda) e na descrição do canal.
- Cada programa de afiliados tem regras próprias de divulgação e de uso da API. Ler os termos antes de publicar.
- Produto pirata/bootleg é comum em marketplaces de figures: a blacklist e a confiança mínima do classifier existem para isso. Na dúvida, não postar.
- O gargalo do negócio é audiência, não código: o canal precisa de divulgação própria (TikTok, Instagram, comunidades de anime) com conteúdo real.
- Comissões variam por categoria e loja; revisar os números reais no relatório de conversões antes de escalar.
