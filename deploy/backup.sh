#!/usr/bin/env bash
# Backup diário do Postgres (pg_dump custom format + gzip) com retenção local.
# O PC do operador puxa o arquivo mais recente via scp (scripts/pull_backup.ps1).
#
# Instalação na VPS:
#   sudo install -o root -g root -m 750 backup.sh /usr/local/sbin/anipromo-backup.sh
#   crontab: 30 3 * * * /usr/local/sbin/anipromo-backup.sh >> /var/log/anipromo-backup.log 2>&1
set -euo pipefail

BACKUP_DIR=/var/backups/anipromo
APP_DIR=/opt/anipromo
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP=$(date +%F_%H%M%S)
OUT="$BACKUP_DIR/deals_${STAMP}.dump.gz"

cd "$APP_DIR"
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-deals}" -Fc "${POSTGRES_DB:-deals}" | gzip > "$OUT"

chmod 600 "$OUT"
ln -sf "$OUT" "$BACKUP_DIR/latest.dump.gz"
find "$BACKUP_DIR" -name 'deals_*.dump.gz' -mtime "+$RETENTION_DAYS" -delete

echo "$(date -Is) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
