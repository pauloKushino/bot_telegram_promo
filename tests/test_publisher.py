from datetime import UTC, datetime, time, timedelta

from deals.publisher.queue import (
    PublishLimits,
    can_publish,
    is_quiet_time,
    local_day_start,
)

LIMITS = PublishLimits(
    max_posts_per_day=20,
    min_post_interval_min=10,
    quiet_start=time(0, 0),
    quiet_end=time(7, 0),
    timezone="America/Sao_Paulo",
)

# 2026-09-30 12:00 UTC = 09:00 em São Paulo (UTC-3, fora do silêncio)
MEIO_DIA_UTC = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_from_quiet_hours():
    limits = PublishLimits.from_quiet_hours(
        "00:00-07:00", max_posts_per_day=20, min_post_interval_min=10, timezone="America/Sao_Paulo"
    )
    assert limits.quiet_start == time(0, 0)
    assert limits.quiet_end == time(7, 0)


def test_publicacao_normal_permitida():
    decision = can_publish(MEIO_DIA_UTC, posts_today=3, last_posted_at=None, paused=False, limits=LIMITS)
    assert decision.allowed


def test_pausado_nunca_publica():
    decision = can_publish(MEIO_DIA_UTC, posts_today=0, last_posted_at=None, paused=True, limits=LIMITS)
    assert not decision.allowed
    assert "pausada" in decision.reason


def test_horario_de_silencio_bloqueia():
    # 04:00 em SP = 07:00 UTC (dentro de 00:00-07:00 local)
    madrugada = datetime(2026, 9, 30, 7, 0, tzinfo=UTC)
    assert is_quiet_time(madrugada, LIMITS)
    decision = can_publish(madrugada, posts_today=0, last_posted_at=None, paused=False, limits=LIMITS)
    assert not decision.allowed
    assert "silêncio" in decision.reason


def test_janela_silencio_que_cruza_meia_noite():
    limits = PublishLimits(
        max_posts_per_day=20,
        min_post_interval_min=10,
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
        timezone="America/Sao_Paulo",
    )
    assert is_quiet_time(datetime(2026, 9, 30, 2, 0, tzinfo=UTC), limits)   # 23:00 SP
    assert is_quiet_time(datetime(2026, 9, 30, 8, 0, tzinfo=UTC), limits)   # 05:00 SP
    assert not is_quiet_time(MEIO_DIA_UTC, limits)                          # 09:00 SP


def test_limite_diario_bloqueia():
    decision = can_publish(MEIO_DIA_UTC, posts_today=20, last_posted_at=None, paused=False, limits=LIMITS)
    assert not decision.allowed
    assert "limite diário" in decision.reason


def test_intervalo_minimo_bloqueia():
    last = MEIO_DIA_UTC - timedelta(minutes=5)
    decision = can_publish(MEIO_DIA_UTC, posts_today=1, last_posted_at=last, paused=False, limits=LIMITS)
    assert not decision.allowed
    assert "intervalo mínimo" in decision.reason


def test_intervalo_minimo_respeitado_libera():
    last = MEIO_DIA_UTC - timedelta(minutes=11)
    decision = can_publish(MEIO_DIA_UTC, posts_today=1, last_posted_at=last, paused=False, limits=LIMITS)
    assert decision.allowed


def test_local_day_start():
    # o início do dia de SP em UTC (meia-noite local = 03:00 UTC em UTC-3)
    start = local_day_start(MEIO_DIA_UTC, "America/Sao_Paulo")
    assert start == datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
