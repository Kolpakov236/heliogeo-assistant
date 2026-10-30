"""Признаки за сутки: история из сырого слоя и расчёт каждого признака на свой момент."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from heliogeo.contracts.features import load_feature_specs
from heliogeo.features.compute import SwpcForecast, compute_features
from heliogeo.features.daily import extract_observation_daily
from heliogeo.features.day import LOOKBACK_DAYS, compute_day_features, end_of_day, lookback_days
from heliogeo.functions.history import data_key, history_from_raw
from heliogeo.functions.stored import day_moment
from heliogeo.mirror.raw import RawStore
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import FETCHED_AT, QUIET_DATA, STORM_DATA, answered_for, load_fixture

SPECS = load_feature_specs()
STORM_DAY = date(2024, 5, 11)
QUIET_DAY = date(2026, 9, 1)


def _row(fixture: str, day: date) -> dict[str, object]:
    return extract_observation_daily(load_fixture(fixture), d=day, raw_s3_key="k")


def _save(store: LocalObjectStore, day: date, body: bytes) -> None:
    RawStore(store).save_snapshot(
        endpoint="data",
        body=body,
        requested_at=day_moment(day),
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )


def _save_fixture(store: LocalObjectStore, fixture: str, day: date) -> None:
    body = answered_for(load_fixture(fixture), day_moment(day))
    _save(store, day, json.dumps(body).encode())


# --- Границы и окно истории ---


def test_lookback_covers_the_longest_feature_window() -> None:
    days = lookback_days(QUIET_DAY)
    assert len(days) == LOOKBACK_DAYS + 1
    assert days[0] == QUIET_DAY - timedelta(days=LOOKBACK_DAYS) and days[-1] == QUIET_DAY
    assert LOOKBACK_DAYS >= 55  # повторяемость через 54,55 суток с окном ±1 сутки


def test_end_of_day_is_midnight_of_the_next_day() -> None:
    assert end_of_day(QUIET_DAY) == datetime(2026, 9, 2, tzinfo=UTC)


# --- История из сырого слоя ---


def test_history_reads_stored_days_and_reports_gaps(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "raw")
    _save_fixture(store, STORM_DATA, STORM_DAY)
    _save(store, date(2024, 5, 12), b"{}")  # тело есть, но на ответ /data не похоже

    result = history_from_raw(store, [date(2024, 5, 10), STORM_DAY, date(2024, 5, 12)])

    assert list(result.rows) == [STORM_DAY]
    assert result.rows[STORM_DAY]["raw_s3_key"] == data_key(STORM_DAY)
    assert result.rows[STORM_DAY]["kp_max"] == 9.0
    assert [day for day, _ in result.gaps] == [date(2024, 5, 10), date(2024, 5, 12)]
    assert "нет" in result.gaps[0][1] and "не разбирается" in result.gaps[1][1]


# --- Признаки за сутки ---


def test_storm_day_values_are_those_of_the_day_itself() -> None:
    row = compute_day_features(STORM_DAY, {STORM_DAY: _row(STORM_DATA, STORM_DAY)}, SPECS)

    assert row.t == end_of_day(STORM_DAY) and row.feature_set_v == SPECS.version
    assert set(row.values) == set(SPECS.by_id())
    assert row.values["kp_max_1d"] == 9.0
    assert row.values["dst_min_1d"] == -406.0
    assert (row.values["flares_x_24h"], row.values["flares_m_24h"]) == (2, 5)
    assert row.values["sw_speed_max"] == 955.0 and row.values["ar_has_delta"] is True
    assert row.values["doy"] == 132  # 11 мая 2024: сами сутки, а не следующие
    assert row.quality["sw_speed_max"] == "degraded"  # OMNI, не точка L1


def test_features_with_a_day_delay_are_not_lost_unlike_a_single_moment() -> None:
    """Выбросы доступны через сутки: на «конец суток» их ещё нет, а за сутки они есть."""
    history = {STORM_DAY: _row(STORM_DATA, STORM_DAY)}
    at_end = compute_features(end_of_day(STORM_DAY), history, SPECS, ids=["cme_earth_directed"])
    day_row = compute_day_features(STORM_DAY, history, SPECS)

    assert at_end.values["cme_earth_directed"] is None
    assert day_row.values["cme_earth_directed"] == 2
    assert day_row.values["cme_earth_directed_speed_max_24h"] == 1263.0


def test_missing_day_gives_missing_not_older_values() -> None:
    previous = STORM_DAY - timedelta(days=1)
    older = {previous: _row(STORM_DATA, previous)}

    row = compute_day_features(STORM_DAY, older, SPECS)

    assert row.values["kp_max_1d"] is None and row.quality["kp_max_1d"] == "missing"
    assert row.values["flares_x_24h"] is None and row.values["cme_count_24h"] is None
    assert row.values["doy"] == 132  # календарь от истории не зависит


def test_trend_needs_the_previous_day_and_other_values_do_not() -> None:
    only_day = compute_day_features(QUIET_DAY, {QUIET_DAY: _row(QUIET_DATA, QUIET_DAY)}, SPECS)
    assert only_day.values["sw_speed_trend_24h"] is None
    assert only_day.values["sw_speed_avg"] == pytest.approx(398.93)

    previous = QUIET_DAY - timedelta(days=1)
    both = {QUIET_DAY: _row(QUIET_DATA, QUIET_DAY), previous: _row(QUIET_DATA, previous)}
    assert compute_day_features(QUIET_DAY, both, SPECS).values["sw_speed_trend_24h"] == 0.0


def test_later_days_in_the_history_do_not_leak_into_the_report() -> None:
    history = {QUIET_DAY: _row(QUIET_DATA, QUIET_DAY)}
    clean = compute_day_features(QUIET_DAY, history, SPECS)

    next_day = QUIET_DAY + timedelta(days=1)
    history[next_day] = _row(STORM_DATA, next_day)  # у следующих суток Kp до 9
    with_future = compute_day_features(QUIET_DAY, history, SPECS)

    assert with_future.values == clean.values
    assert clean.values["kp_max_1d"] == pytest.approx(2.333)


def test_swpc_forecast_is_the_last_one_issued_before_the_end_of_the_day() -> None:
    history = {QUIET_DAY: _row(QUIET_DATA, QUIET_DAY)}
    inside = SwpcForecast(datetime(2026, 9, 1, 22, 0, tzinfo=UTC), (2.0, 6.3, 3.0))
    after = SwpcForecast(datetime(2026, 9, 2, 1, 0, tzinfo=UTC), (9.0,))

    row = compute_day_features(QUIET_DAY, history, SPECS, forecasts=[inside, after])

    assert row.values["swpc_g_scale"] == 2  # G2 по максимуму 6.3; выпуск после суток не учтён
    assert compute_day_features(QUIET_DAY, history, SPECS).values["swpc_g_scale"] is None


def test_history_from_raw_feeds_the_day_features_end_to_end(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "raw")
    _save_fixture(store, QUIET_DATA, QUIET_DAY)

    history = history_from_raw(store, lookback_days(QUIET_DAY))
    row = compute_day_features(QUIET_DAY, history.rows, SPECS)

    assert len(history.rows) == 1 and len(history.gaps) == LOOKBACK_DAYS
    assert row.values["ar_count"] == 6 and row.values["flares_c_24h"] == 3
    assert row.values["kp_max_1d"] == pytest.approx(2.333)
