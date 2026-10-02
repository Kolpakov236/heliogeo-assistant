"""Проверка самих мок-фабрик (tests/mocks/forecast.py): данные согласованы и предсказуемы."""

import math
from datetime import timedelta

import pytest

from tests.mocks.forecast import (
    ISSUED_AT,
    LEVELS,
    SOURCES,
    make_distribution,
    make_forecast,
    make_issue_forecasts,
    make_rolling_metrics,
    make_verification,
)


@pytest.mark.parametrize("peak", [None, *LEVELS])
def test_distribution_sums_to_one_and_covers_all_levels(peak: str | None) -> None:
    """Сумма вероятностей — 1, ключи — все уровни."""
    distribution = make_distribution(peak)
    assert set(distribution) == set(LEVELS)
    assert sum(distribution.values()) == pytest.approx(1.0, abs=1e-5)


def test_peaked_distribution_has_its_maximum_at_peak() -> None:
    """Максимум пиковой разметки — на заданном уровне."""
    distribution = make_distribution("G1")
    assert max(distribution, key=distribution.__getitem__) == "G1"
    assert distribution["G1"] == 0.6


def test_unknown_peak_is_rejected() -> None:
    with pytest.raises(ValueError, match="неизвестный уровень"):
        make_distribution("G9")


def test_forecast_defaults() -> None:
    """Прогноз по умолчанию: геомагнитный уровень, сутки 1 после выпуска, климатология."""
    forecast = make_forecast()
    assert (forecast.quantity, forecast.horizon_days, forecast.source) == (
        "geomag_level",
        1,
        "climatology",
    )
    assert forecast.issued_at == ISSUED_AT
    assert forecast.target_from == ISSUED_AT
    assert forecast.target_to == ISSUED_AT + timedelta(days=1)
    assert forecast.point_estimate == "quiet"
    assert forecast.id is None  # id выдаёт БД


def test_target_window_follows_horizon() -> None:
    """Горизонт N — N-е сутки после выпуска."""
    forecast = make_forecast(horizon_days=3)
    assert forecast.target_from == ISSUED_AT + timedelta(days=2)
    assert forecast.target_to == ISSUED_AT + timedelta(days=3)


def test_overrides_win_over_defaults() -> None:
    forecast = make_forecast(
        source="ml",
        target_from=ISSUED_AT + timedelta(hours=6),
        distribution=make_distribution("G2"),
    )
    assert forecast.source == "ml"
    assert forecast.target_from == ISSUED_AT + timedelta(hours=6)
    assert forecast.point_estimate == "G2"


def test_issue_forecasts_cover_all_sources_and_horizons() -> None:
    """Выпуск: 5 источников × 3 горизонта, ключи уникальны."""
    forecasts = make_issue_forecasts()
    keys = {(f.issued_at, f.quantity, f.horizon_days, f.source) for f in forecasts}
    assert len(forecasts) == len(keys) == len(SOURCES) * 3


def test_verification_metrics_are_consistent_with_distribution() -> None:
    """Brier, RPS, log score и hit совпадают с расчётом по определению (известный ответ)."""
    forecast = make_forecast(distribution={"quiet": 0.7, "unsettled": 0.3})

    hit = make_verification(forecast, "quiet", forecast_id=1)
    assert hit.brier == pytest.approx(0.18)
    assert hit.rps == pytest.approx(0.09)
    assert hit.log_score == pytest.approx(-math.log(0.7))
    assert hit.hit is True

    miss = make_verification(forecast, "unsettled", forecast_id=1)
    assert miss.brier == pytest.approx(0.98)
    assert miss.rps == pytest.approx(0.49)
    assert miss.hit is False


def test_verification_defaults() -> None:
    forecast = make_forecast()
    verification = make_verification(forecast, "G1", obs_is_final=False, forecast_id=7)
    assert (verification.forecast_id, verification.obs_source) == (7, "gfz")
    assert (verification.observed_value, verification.obs_is_final) == (5.0, False)


def test_verification_requires_saved_forecast() -> None:
    """Без id прогноза верификацию не собрать — чтобы не получить forecast_id=None."""
    with pytest.raises(ValueError, match="сначала сохраните прогноз"):
        make_verification(make_forecast())


def test_verification_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="неизвестный уровень"):
        make_verification(make_forecast(), "G9", forecast_id=1)


def test_rolling_metrics_defaults_and_overrides() -> None:
    metrics = make_rolling_metrics(source="swpc", n=60)
    assert (metrics.source, metrics.n, metrics.window_days, metrics.horizon_days) == (
        "swpc",
        60,
        90,
        1,
    )
    assert metrics.tss_g2 is None
