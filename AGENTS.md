# AGENTS.md — Bot de Promoções de Anime (AniPromo)

Contexto operacional para agentes que retomarem este projeto.

## Estado (2026-10-02)

- **Desenvolvimento pausado**, sistema **rodando em produção local** no PC do usuário
  (Docker Desktop + `docker compose up -d`): bot @anipromo_bot, canal AniPromos (privado),
  worker coletando a cada 20 min, ~120 produtos, histórico acumulando até o deal engine
  atingir 7 dias (`MIN_HISTORY_DAYS`).
- Boot do PC: Docker Desktop sobe no login (autostart ativado) e os containers voltam
  (`restart: unless-stopped`). PC configurado para não suspender/hibernar na tomada.
- Fases concluídas: **1, 2 e 3a** (ver README.md: fases). VPS pausada (usuário sem cartão no
  momento — runbook pronto em `deploy/README.md`, `docker-compose.prod.yml` pronto).

## Specs de referência na raiz

- `planejamento-bot-promocoes-anime.md` — spec original (fases 1-4)
- `fase3-bot-promocoes-anime.md` — decisões da fase 3 (freemium, preços, ordem)

## Ambiente e armadilhas desta máquina

- `uv` gerencia Python. **Fixe 3.12** (`.python-version` existe — não recrie com 3.15 beta,
  o asyncpg não compila lá).
- Portas: 5432 = Postgres nativo do Windows (NÃO mexer, é de outro projeto);
  **15432** = compose daqui (host). Dentro do compose o host é `postgres:5432`.
- PowerShell corrompe `python -c` com aspas → use scripts em `scripts/` com
  `uv run python -X utf8`. Console cp1252 → `sys.stdout.reconfigure(encoding="utf-8")` nos scripts.
- `ruff` line-length 110; `migrations/` excluído do lint; testes com
  `TEST_DATABASE_URL=postgresql+asyncpg://deals:deals_dev_password@localhost:15432/deals_test`.

## Lições de API já validadas (não re-descobrir)

- **Shopee Afiliados** (2026-10): assinatura = SHA256 puro `AppID+Timestamp+Payload+Secret`
  (não HMAC); `subIds` só alfanuméricos (sem hífen); `Int64` como string nas variáveis;
  `pageInfo` null quando sem conversões (query não pede pageInfo). Detalhes em
  `src/deals/stores/shopee.py` e `scripts/introspect_shopee.py`.
- **IA**: NVIDIA NIM (SDK openai, `AI_BASE_URL`), modelo atual `nvidia/nemotron-3-ultra-550b-a55b`
  (escolhido por benchmark real em `scripts/benchmark_models.py`; fallback medido:
  `moonshotai/kimi-k3`). Tier free é lento (30–90s/chamada) e dá 503 — o pipeline tolera
  (retry + cache). O usuário pediu NVIDIA em vez do Claude da spec.
- **Telegram**: callbacks limitam a 64 bytes (franquias vão por hash md5 de 12 chars);
  link de canal privado é criado pelo bot e cacheado em `bot_settings` (não criar um novo
  a cada /start).
- **Regra de negócio**: "menor preço em 60 dias" = estritamente abaixo do mínimo dos 60 dias
  ANTERIORES (exclui a observação atual) — desvio documentado da spec literal, pois preço
  estável sempre seria "menor preço".

## Ferramentas em `scripts/`

check_env (formato das credenciais sem expor), validate_telegram / validate_shopee /
validate_shortlink / validate_conversions / validate_pipeline / validate_publish (APIs reais),
benchmark_models (escolha de modelo NIM), check_status + db_peek (inspeção), add_track_buttons
(retrofit de botões em posts antigos), tg_login + e2e_admin + e2e_user (E2E real via Telethon —
sessão `userbot.session` está no PC, fora do git; acesso total à conta do usuário, revogável em
Telegram → Dispositivos).

## Regras ao mexer aqui

1. Sistema em uso: qualquer mudança = rebuild das imagens (`docker compose build --no-cache` se
   `pyproject`/lock mudar) + `docker compose up -d`. Nunca `docker compose down` sem avisar.
2. Segredos só no `.env`; `userbot.session` nunca no git.
3. Commits pequenos; rodar `pytest` + `ruff` antes de commitar.
4. Fases 3b/3c (Stripe) e 4 (Mercado Livre/Amazon) pendentes — dependem de VPS e da leitura dos
   termos de afiliados Shopee pelo usuário antes de cobrar premium.
