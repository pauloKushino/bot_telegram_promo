"""Limites de publicação (spec 7.5) — lógica pura e testável.

O acesso ao banco fica nos jobs do worker; aqui só decisões com dados prontos.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class PublishLimits:
    max_posts_per_day: int = 20
    min_post_interval_min: int = 10
    quiet_start: time = time(0, 0)
    quiet_end: time = time(7, 0)
    timezone: str = "America/Sao_Paulo"

    @classmethod
    def from_quiet_hours(
        cls,
        quiet_hours: str,
        *,
        max_posts_per_day: int,
        min_post_interval_min: int,
        timezone: str,
    ) -> "PublishLimits":
        """quiet_hours no formato "HH:MM-HH:MM" (ex.: "00:00-07:00")."""
        start_s, _, end_s = quiet_hours.partition("-")
        quiet_start = time.fromisoformat(start_s.strip())
        quiet_end = time.fromisoformat(end_s.strip())
        return cls(
            max_posts_per_day=max_posts_per_day,
            min_post_interval_min=min_post_interval_min,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            timezone=timezone,
        )


@dataclass(frozen=True)
class PublishDecision:
    allowed: bool
    reason: str


def to_local(now_utc: datetime, tz: str) -> datetime:
    return now_utc.astimezone(ZoneInfo(tz))


def local_day_start(now_utc: datetime, tz: str) -> datetime:
    """Meia-noite de hoje no fuso local, devolvida em UTC (p/ comparar com o banco)."""
    local = to_local(now_utc, tz)
    midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(UTC)


def is_quiet_time(now_utc: datetime, limits: PublishLimits) -> bool:
    """Horário de silêncio — suporta janela que cruza meia-noite (ex.: 22:00-07:00)."""
    local_now = to_local(now_utc, limits.timezone).time()
    start, end = limits.quiet_start, limits.quiet_end
    if start < end:
        return start <= local_now < end
    # Janela que cruza a meia-noite
    return local_now >= start or local_now < end


def can_publish(
    now_utc: datetime,
    *,
    posts_today: int,
    last_posted_at: datetime | None,
    paused: bool,
    limits: PublishLimits,
) -> PublishDecision:
    """Decisão única de publicação: pausa, silêncio, teto diário e intervalo mínimo."""
    if paused:
        return PublishDecision(False, "publicação pausada pelo admin")

    if is_quiet_time(now_utc, limits):
        return PublishDecision(
            False,
            f"horário de silêncio ({limits.quiet_start:%H:%M}-{limits.quiet_end:%H:%M} {limits.timezone})",
        )

    if posts_today >= limits.max_posts_per_day:
        return PublishDecision(False, f"limite diário atingido ({posts_today}/{limits.max_posts_per_day})")

    if last_posted_at is not None:
        next_allowed = last_posted_at + timedelta(minutes=limits.min_post_interval_min)
        if now_utc < next_allowed:
            wait_min = int((next_allowed - now_utc).total_seconds() // 60) + 1
            return PublishDecision(False, f"intervalo mínimo: aguardar ~{wait_min} min")

    return PublishDecision(True, "ok")
