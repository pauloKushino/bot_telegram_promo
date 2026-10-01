# Baixa o backup mais recente da VPS para este PC (retém os últimos 14 locais).
# Agende no Task Scheduler (diário), ex.:
#   powershell -NoProfile -File C:\Users\User\Desktop\bot_telegram_promo\scripts\pull_backup.ps1
#
# Configure antes (ou crie scripts\backup.config.ps1 com as variáveis):
#   $env:ANIPROMO_VPS_HOST, $env:ANIPROMO_VPS_USER (padrão root), $env:ANIPROMO_BACKUP_DIR

$ErrorActionPreference = "Stop"

$hostVps = if ($env:ANIPROMO_VPS_HOST) { $env:ANIPROMO_VPS_HOST } else { "IP_DA_VPS" }
$userVps = if ($env:ANIPROMO_VPS_USER) { $env:ANIPROMO_VPS_USER } else { "root" }
$key = "$env:USERPROFILE\.ssh\id_ed25519_anipromo"
$localDir = if ($env:ANIPROMO_BACKUP_DIR) { $env:ANIPROMO_BACKUP_DIR } else { "$env:USERPROFILE\backups\anipromo" }

New-Item -ItemType Directory -Force -Path $localDir | Out-Null

$stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$dest = Join-Path $localDir "deals_$stamp.dump.gz"

Write-Output "Baixando backup de ${userVps}@${hostVps} ..."
scp -i $key -o StrictHostKeyChecking=accept-new "${userVps}@${hostVps}:/var/backups/anipromo/latest.dump.gz" $dest

if ($LASTEXITCODE -eq 0) {
    Write-Output "Backup salvo em $dest"
    Get-ChildItem $localDir -Filter "deals_*.dump.gz" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 14 |
        Remove-Item
} else {
    Write-Error "scp falhou (codigo $LASTEXITCODE)"
}
