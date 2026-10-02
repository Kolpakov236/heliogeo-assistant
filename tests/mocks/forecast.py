"""Моки строк таблиц прогнозов: ForecastRecord, ForecastVerificationRecord, RollingMetricsRecord.

Правдоподобные значения из ЭТАП1 (§3.6, §7), которые создаются без БД и точечно
переопределяются: ``make_forecast(source="swpc", horizon_days=2)``. Метрики верификации
считаются упрощённо — лишь чтобы тестовые данные были согласованы между собой; эталонная
реализация метрик появится в модуле прогнозов (задача B4-06).
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from heliogeo.storage.models import (
    ForecastRecord,
    ForecastVerificationRecord,
    RollingMetricsRecord,
)

# Уровни геомагнитной активности, от меньшего к большему (ЭТАП1 §2.4)
LEVELS = ("quiet", "unsettled", "active", "G1", "G2", "G3+")
SOURCES = ("climatology", "persistence", "recurrence", "swpc", "ml")
ISSUED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# Климатология из ЭТАП1 §7 (там «G2+» = 0,04, здесь разложено на G2 и G3+)
_CLIMATOLOGY = {
    "quiet": 0.42,
    "unsettled": 0.28,
    "active": 0.17,
    "G1": 0.09,
    "G2": 0.03,
    "G3+": 0.01,
}
# Типичный Kp для каждого уровня — значение observed_value в верификации
_KP = {"quiet": 1.0, "unsettled": 3.0, "active": 4.0, "G1": 5.0, "G2": 6.0, "G3+": 7.0}


def make_distribution(peak: str | None = None) -> dict[str, float]:
    """Распределение по уровням: климатология или с максимумом 0,6 на уровне ``peak``."""
    if peak is None:
        return dict(_CLIMATOLOGY)
    if peak not in LEVELS:
        raise ValueError(f"неизвестный уровень: {peak}")
    rest = {level: p for level, p in _CLIMATOLOGY.items() if level != peak}
    scale = 0.4 / sum(rest.values())
    return {level: 0.6 if level == peak else round(rest[level] * scale, 6) for level in LEVELS}


def make_forecast(**overrides: Any) -> ForecastRecord:
    """Прогноз геомагнитного уровня: выпуск 2026-09-01 12:00 UTC, горизонт 1 сутки.

    Целевое окно — сутки номер ``horizon_days`` после выпуска; ``point_estimate`` — самый
    вероятный уровень. Любое поле переопределяется именованным аргументом.
    """
    values: dict[str, Any] = {
        "issued_at": ISSUED_AT,
        "quantity": "geomag_level",
        "horizon_days": 1,
        "source": "climatology",
        "distribution": make_distribution(),
        "features_t": ISSUED_AT,
        "feature_set_v": "1",
    }
    values.update(overrides)
    values.setdefault(
        "target_from", values["issued_at"] + timedelta(days=values["horizon_days"] - 1)
    )
    values.setdefault("target_to", values["target_from"] + timedelta(days=1))
    if "point_estimate" not in values:  # лениво: distribution может быть намеренно некорректным
        values["point_estimate"] = max(values["distribution"], key=values["distribution"].get)
    return ForecastRecord(**values)


def make_issue_forecasts(
    issued_at: datetime = ISSUED_AT,
    sources: tuple[str, ...] = SOURCES,
    horizons: tuple[int, ...] = (1, 2, 3),
) -> list[ForecastRecord]:
    """Все прогнозы одного выпуска: каждый источник на каждый горизонт (как в ЭТАП1 §7)."""
    return [
        make_forecast(issued_at=issued_at, source=source, horizon_days=horizon)
        for source in sources
        for horizon in horizons
    ]


def _brier(distribution: dict[str, float], observed: str) -> float:
    return sum(
        (distribution.get(level, 0.0) - (1.0 if level == observed else 0.0)) ** 2
        for level in LEVELS
    )


def _rps(distribution: dict[str, float], observed: str) -> float:
    """Ранжированный вероятностный балл (ненормированный)."""
    cum_forecast = cum_observed = total = 0.0
    for level in LEVELS[:-1]:
        cum_forecast += distribution.get(level, 0.0)
        cum_observed += 1.0 if level == observed else 0.0
        total += (cum_forecast - cum_observed) ** 2
    return total


def make_verification(
    forecast: ForecastRecord,
    observed_level: str = "quiet",
    obs_is_final: bool = True,
    **overrides: Any,
) -> ForecastVerificationRecord:
    """Верификация прогноза; brier, rps и hit согласованы с его распределением.

    Прогноз должен быть уже сохранён (flush), чтобы у него был ``id``.
    """
    if observed_level not in LEVELS:
        raise ValueError(f"неизвестный уровень: {observed_level}")
    if forecast.id is None and "forecast_id" not in overrides:
        raise ValueError("сначала сохраните прогноз (session.flush()), чтобы получить его id")
    distribution = forecast.distribution
    values: dict[str, Any] = {
        "forecast_id": forecast.id,
        "observed_level": observed_level,
        "observed_value": _KP[observed_level],
        "obs_source": "gfz",
        "obs_is_final": obs_is_final,
        "brier": _brier(distribution, observed_level),
        "rps": _rps(distribution, observed_level),
        "log_score": -math.log(max(distribution.get(observed_level, 0.0), 1e-12)),
        "hit": forecast.point_estimate == observed_level,
    }
    values.update(overrides)
    return ForecastVerificationRecord(**values)


def make_rolling_metrics(**overrides: Any) -> RollingMetricsRecord:
    """Скользящие метрики за 90 суток (значения персистентности из панели ЭТАП1 §3.6)."""
    values: dict[str, Any] = {
        "computed_at": datetime(2026, 9, 2, 0, 30, tzinfo=UTC),
        "quantity": "geomag_level",
        "horizon_days": 1,
        "source": "persistence",
        "window_days": 90,
        "n": 90,
        "bss": 0.094,
        "rps_mean": 0.138,
        "reliability": 0.021,
        "resolution": 0.0071,
        "sharpness": 0.24,
        "tss_g1": 0.31,
        "tss_g2": None,
    }
    values.update(overrides)
    return RollingMetricsRecord(**values)
