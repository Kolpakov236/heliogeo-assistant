"""Прогноз Kp: контракт результата, заглушки на месте моделей, устойчивость к сбоям модели."""

import json
from datetime import UTC, date, datetime

import pytest

from heliogeo.features.compute import History
from heliogeo.forecast.kp import (
    KpForecast,
    KpForecastStatus,
    StubKpForecaster,
    default_kp_forecasters,
    run_forecasters,
)

ISSUED = datetime(2026, 9, 2, 0, 30, tzinfo=UTC)
GRID_START = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
HISTORY: History = {date(2026, 9, 1): {"kp_values": [1.0] * 8}}


class _GoodModel:
    name = "kp_good"

    def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
        return KpForecast(
            model=self.name,
            status=KpForecastStatus.OK,
            issued_utc=issued_utc,
            start_utc=GRID_START,
            values=(2.0, 3.0, 5.33, 4.0, 3.0, 2.0, 2.0, 1.0),
        )


class _BrokenModel:
    name = "kp_broken"

    def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
        raise RuntimeError("нет весов модели")


def _ok(**overrides: object) -> KpForecast:
    fields: dict[str, object] = {
        "model": "m",
        "status": KpForecastStatus.OK,
        "issued_utc": ISSUED,
        "start_utc": GRID_START,
        "values": (2.0, 3.0),
    }
    return KpForecast(**{**fields, **overrides})  # type: ignore[arg-type]


# --- Результат прогноза ---


def test_ok_forecast_exposes_horizon_maximum_and_json() -> None:
    forecast = _GoodModel().forecast(HISTORY, ISSUED)

    assert forecast.usable and forecast.horizon_hours == 24 and forecast.max_kp == 5.33
    payload = forecast.to_json()
    assert payload["status"] == "ok" and payload["start_utc"] == "2026-09-02T00:00:00+00:00"
    assert payload["values"][2] == 5.33 and payload["horizon_hours"] == 24
    json.dumps(payload)  # только JSON-типы


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"issued_utc": datetime(2026, 9, 2)}, "часовым поясом"),
        ({"values": None}, "только у прогноза со статусом ok"),
        ({"values": ()}, "не может быть пустой"),
        ({"start_utc": None}, "нужен start_utc"),
        ({"start_utc": datetime(2026, 9, 2)}, "нужен start_utc"),
        ({"values": (2.0, 9.5)}, "от 0 до 9"),
        ({"values": (-0.1,)}, "от 0 до 9"),
        ({"status": KpForecastStatus.STUB}, "только у прогноза со статусом ok"),
    ],
    ids=[
        "naive-issue",
        "ok-without-values",
        "empty-grid",
        "no-start",
        "naive-start",
        "above-nine",
        "below-zero",
        "stub-with-values",
    ],
)
def test_forecast_rejects_inconsistent_fields(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _ok(**overrides)


# --- Заглушки ---


def test_stub_says_the_model_is_not_connected_and_invents_nothing() -> None:
    forecast = StubKpForecaster("kp_gbm", "бустинг").forecast(HISTORY, ISSUED)

    assert forecast.model == "kp_gbm" and forecast.status is KpForecastStatus.STUB
    assert forecast.values is None and forecast.max_kp is None and not forecast.usable
    assert forecast.horizon_hours == 0
    assert "не подключена" in (forecast.note or "") and "kp_gbm" in (forecast.note or "")
    assert forecast.to_json()["values"] is None


def test_default_registry_has_named_places_for_the_future_models() -> None:
    forecasters = default_kp_forecasters()

    assert [f.name for f in forecasters] == ["kp_gbm", "kp_recurrence", "kp_persistence"]
    assert all(f.forecast(HISTORY, ISSUED).status is KpForecastStatus.STUB for f in forecasters)


# --- Опрос моделей ---


def test_run_keeps_order_and_replaces_stubs_with_real_models() -> None:
    models = (_GoodModel(), *default_kp_forecasters()[1:])

    forecasts = run_forecasters(models, HISTORY, ISSUED)

    assert [f.model for f in forecasts] == ["kp_good", "kp_recurrence", "kp_persistence"]
    assert [f.usable for f in forecasts] == [True, False, False]


def test_a_failing_model_becomes_unavailable_and_does_not_stop_the_others() -> None:
    forecasts = run_forecasters((_BrokenModel(), _GoodModel()), HISTORY, ISSUED)

    broken, good = forecasts
    assert broken.status is KpForecastStatus.UNAVAILABLE and not broken.usable
    assert "RuntimeError" in (broken.note or "") and "нет весов" in (broken.note or "")
    assert good.usable


def test_no_models_gives_no_forecasts() -> None:
    assert run_forecasters((), HISTORY, ISSUED) == ()
