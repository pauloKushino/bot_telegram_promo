# Deploy em VPS (produção)

Bot de promoções em produção na DigitalOcean (ou qualquer VPS com Docker).
Sem portas públicas além do SSH nesta fase (o bot usa polling; o webhook/Stripe entra na fase 3b).

## Topologia

```
VPS (Ubuntu 24.04, 2GB)
└── docker compose -f docker-compose.prod.yml
    ├── postgres   (rede interna apenas, volume pgdata)
    ├── migrate    (alembic upgrade head a cada deploy)
    ├── bot        (polling; comandos admin)
    └── worker     (coleta/classificação/publicação/relatórios)

backup.sh (cron 03:30 UTC) → /var/backups/anipromo/latest.dump.gz
scripts/pull_backup.ps1 (Task Scheduler no PC do operador) → scp diário p/ o PC
healthchecks.io (opcional) → ping a cada 10 min do worker; mudo = e-mail de alerta
```

## 1. Criar o droplet

- DigitalOcean → Create Droplet: **Ubuntu 24.04 LTS**, **2 GB RAM / 1 vCPU**, região **São Paulo** (se disponível)
- SSH key: adicione a chave pública gerada na máquina do operador
  (`C:\Users\User\.ssh\id_ed25519_anipromo.pub`)
- Anote o IP público.

## 2. Endurecimento mínimo

```bash
ufw allow OpenSSH && ufw enable
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh
```

## 3. Docker

```bash
apt update && apt install -y docker.io docker-compose-v2
systemctl enable --now docker
```

## 4. Código e configuração

```bash
# se o repo for privado, gere uma deploy key na VPS e cadastre no GitHub (read-only)
ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519_deploy -N "" -C "anipromo-deploy-key"
cat /root/.ssh/id_ed25519_deploy.pub   # adicionar em GitHub → repo → Settings → Deploy keys

mkdir -p /opt && git clone git@github.com:pauloKushino/bot_telegram_promo.git /opt/anipromo
cd /opt/anipromo
cp deploy/.env.production.example .env
# editar .env com os valores reais; depois:
chmod 600 .env
openssl rand -base64 32   # use como POSTGRES_PASSWORD (e repita-a em DATABASE_URL)
```

## 5. Migrar os dados locais (histórico de preço acumulado)

No PC (PowerShell, na pasta do projeto):

```powershell
# dump do banco DE DESENVOLVIMENTO local
docker compose exec -T postgres pg_dump -U deals -Fc deals | Set-Content -Encoding Byte deals_local.dump
# enviar para a VPS
scp -i $env:USERPROFILE\.ssh\id_ed25519_anipromo deals_local.dump root@IP_DA_VPS:/root/
# apagar o dump local depois de confirmar o restore
Remove-Item deals_local.dump
```

Na VPS:

```bash
cd /opt/anipromo
docker compose -f docker-compose.prod.yml up -d postgres
docker exec -i $(docker compose -f docker-compose.prod.yml ps -q postgres) \
    pg_restore -U deals -d deals --clean --if-exists --no-owner < /root/deals_local.dump
rm /root/deals_local.dump
```

## 6. Subir a stack

```bash
cd /opt/anipromo
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f worker
```

Verificação: bot responde `/stats`; `docker compose ps` mostra tudo `Up`.

## 7. Backup diário

```bash
sudo install -o root -g root -m 750 deploy/backup.sh /usr/local/sbin/anipromo-backup.sh
( crontab -l 2>/dev/null; echo "30 3 * * * /usr/local/sbin/anipromo-backup.sh >> /var/log/anipromo-backup.log 2>&1" ) | crontab -
```

No Windows, agende `scripts/pull_backup.ps1` (Task Scheduler, diário) com `ANIPROMO_VPS_HOST=IP_DA_VPS`.

## 8. Monitor de disponibilidade (opcional, grátis)

Crie um check em https://healthchecks.io (intervalo 10 min), copie a ping URL para
`HEALTHCHECK_PING_URL` no `.env` e recrie o worker. Worker parado = e-mail de alerta.

## 9. Rotina de deploy (atualizações)

```bash
cd /opt/anipromo && git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f --tail 50
```

## 10. Migração para outra VPS no futuro

1. Na VPS nova: passos 2-4 (com o mesmo `.env`).
2. Restaure o backup mais recente (passo 5, usando `/var/backups/anipromo/latest.dump.gz`).
3. Suba a stack (passo 6), valide, só então desligue a VPS antiga.
Nada no Telegram/Stripe aponta para o IP antigo nesta fase (polling). Na fase 3b+,
lembre de atualizar DNS + webhook do Stripe.
