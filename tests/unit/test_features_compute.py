"""Строка признаков на момент t: все признаки контракта, задержки доступности, окна, качество."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from heliogeo.contracts.features import FeatureGroup, FeatureSpec, load_feature_specs
from heliogeo.features.compute import COMPUTERS, SwpcForecast, compute_features
from heliogeo.features.daily import extract_observation_daily
from tests.mocks.swx import QUIET_DATA, STORM_DATA, load_fixture

SPECS = load_feature_specs()
T = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)


def day(**fields: Any) -> dict[str, Any]:
    """Строка суточных наблюдений: нужные поля, остальное — «нет данных»."""
    return {"qc": {}, **fields}


def history(**by_day: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """``history(d2026_09_01=day(...))`` → {date(2026, 9, 1): ...}."""
    return {date(int(k[1:5]), int(k[6:8]), int(k[9:11])): v for k, v in by_day.items()}


def one(feature_id: str, t: datetime, hist: dict[date, dict[str, Any]], **kwargs: Any) -> Any:
    row = compute_features(t, hist, SPECS, ids=[feature_id], **kwargs)
    return row.values[feature_id], row.quality[feature_id]


# --- Состав ---


def test_every_spec_has_a_computer_and_back() -> None:
    assert set(COMPUTERS) == set(SPECS.by_id())
    assert len(SPECS.features) == 40


def test_row_contains_all_forty_features() -> None:
    row = compute_features(T, {}, SPECS)
    assert list(row.values) == [f.id for f in SPECS.features]
    assert set(row.quality) == set(row.values)
    assert row.feature_set_v == SPECS.version
    assert row.t == T


def test_empty_history_gives_only_calendar_values() -> None:
    row = compute_features(T, {}, SPECS)
    present = {k for k, v in row.values.items() if v is not None}
    assert present == {"doy"}
    assert {q for k, q in row.quality.items() if k != "doy"} == {"missing"}


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        compute_features(datetime(2026, 9, 2, 6, 0), {}, SPECS)


def test_time_in_other_zone_is_converted_to_utc() -> None:
    moscow = datetime(2026, 9, 2, 9, 0, tzinfo=timezone(timedelta(hours=3)))
    assert compute_features(moscow, {}, SPECS).t == T


def test_unknown_feature_id_is_an_error() -> None:
    with pytest.raises(ValueError, match="нет в контракте"):
        compute_features(T, {}, SPECS, ids=["nope"])


def test_feature_without_computer_is_an_error_not_a_silent_gap() -> None:
    extra = FeatureSpec(
        id="brand_new",
        group=FeatureGroup.CALENDAR,
        description="новый признак без вычислителя",
        available_delay=timedelta(0),
    )
    specs = SPECS.model_copy(update={"features": (*SPECS.features, extra)})
    with pytest.raises(ValueError, match="brand_new"):
        compute_features(T, {}, specs)


# --- Задержки доступности: данные из будущего не берутся ---


def test_solar_wind_day_is_used_only_after_its_end_plus_delay() -> None:
    hist = history(d2026_08_31=day(sw_speed_avg=100.0), d2026_09_01=day(sw_speed_avg=200.0))
    # t - 1 ч = 09-01 23:30: сутки 09-01 ещё не закончились → берутся 08-31
    assert one("sw_speed_avg", datetime(2026, 9, 2, 0, 30, tzinfo=UTC), hist)[0] == 100.0
    # t - 1 ч = 09-02 00:00: сутки 09-01 закончились
    assert one("sw_speed_avg", datetime(2026, 9, 2, 1, 0, tzinfo=UTC), hist)[0] == 200.0


def test_cme_delay_is_one_day() -> None:
    hist = history(d2026_08_31=day(cme_count=2), d2026_09_01=day(cme_count=7))
    assert one("cme_count_24h", datetime(2026, 9, 2, 1, 0, tzinfo=UTC), hist)[0] == 2
    assert one("cme_count_24h", datetime(2026, 9, 3, 0, 0, tzinfo=UTC), hist)[0] == 7


def test_missing_day_is_missing_not_replaced_by_older_one() -> None:
    hist = history(d2026_08_30=day(sw_speed_avg=100.0))
    assert one("sw_speed_avg", T, hist) == (None, "missing")


def test_future_rows_in_history_are_ignored() -> None:
    hist = history(d2026_09_01=day(ar_count=5), d2026_09_02=day(ar_count=99))
    assert one("ar_count", T, hist)[0] == 5


# --- Kp: окна по трёхчасовой сетке ---

RISING = [round(0.1 * i, 1) for i in range(1, 9)]  # 0.1 … 0.8


def test_kp_cur_is_slot_that_ended_at_cutoff() -> None:
    hist = history(d2026_09_01=day(kp_values=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]))
    # t - 3 ч = 12:00 → последний закончившийся интервал 09–12 (четвёртый)
    assert one("kp_cur", datetime(2026, 9, 1, 15, 0, tzinfo=UTC), hist) == (4.0, "ok")
    # 14:59 - 3 ч = 11:59 → граница округляется вниз до 09:00 → интервал 06–09
    assert one("kp_cur", datetime(2026, 9, 1, 14, 59, tzinfo=UTC), hist)[0] == 3.0


def test_kp_cur_is_missing_when_latest_slot_is_absent() -> None:
    hist = history(d2026_09_01=day(kp_values=RISING))
    assert one("kp_cur", datetime(2026, 9, 3, 12, 0, tzinfo=UTC), hist) == (None, "missing")


def test_kp_max_and_mean_over_full_day() -> None:
    hist = history(d2026_09_01=day(kp_values=RISING))
    t = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)  # cutoff = 09-02 00:00
    assert one("kp_max_1d", t, hist) == (0.8, "ok")
    value, quality = one("kp_mean_1d", t, hist)
    assert value == pytest.approx(0.45)
    assert quality == "ok"


def test_kp_window_without_data_after_cutoff() -> None:
    hist = history(d2026_09_01=day(kp_values=RISING), d2026_09_02=day(kp_values=[9.0] * 8))
    t = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)
    assert one("kp_max_1d", t, hist)[0] == 0.8  # 09-02 ещё не началось к cutoff


def test_incomplete_window_is_degraded() -> None:
    hist = history(d2026_09_01=day(kp_values=RISING))
    value, quality = one("kp_max_3d", datetime(2026, 9, 2, 3, 0, tzinfo=UTC), hist)
    assert value == 0.8
    assert quality == "degraded"


def test_day_with_short_kp_list_is_skipped() -> None:
    hist = history(d2026_09_01=day(kp_values=[1.0, 2.0, 3.0]))
    assert one("kp_max_1d", datetime(2026, 9, 2, 3, 0, tzinfo=UTC), hist) == (None, "missing")


def test_kp_trend_is_slope_per_day() -> None:
    kp = [round(0.1 * i, 1) for i in range(1, 25)]  # +0.1 за интервал = 0.8 в сутки
    hist = history(
        d2026_08_30=day(kp_values=kp[:8]),
        d2026_08_31=day(kp_values=kp[8:16]),
        d2026_09_01=day(kp_values=kp[16:]),
    )
    value, quality = one("kp_trend_3d", datetime(2026, 9, 2, 3, 0, tzinfo=UTC), hist)
    assert value == pytest.approx(0.8)
    assert quality == "ok"


def test_kp_trend_needs_two_points() -> None:
    assert one("kp_trend_3d", T, {}) == (None, "missing")


def test_kp_recurrence_takes_window_around_one_solar_rotation() -> None:
    t = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)  # центр окна ≈ 2026-08-13 17:23
    quiet = [1.0] * 8
    hist = history(
        d2026_08_12=day(kp_values=quiet),
        d2026_08_13=day(kp_values=quiet),
        d2026_08_14=day(kp_values=[7.0, *quiet[1:]]),  # в окне
        d2026_08_15=day(kp_values=[9.0, *quiet[1:]]),  # за окном
        d2026_08_10=day(kp_values=[9.0] * 8),  # за окном
    )
    value, quality = one("kp_recurrence_27d", t, hist)
    assert value == 7.0
    assert quality == "ok"


def test_kp_recurrence_54d_looks_further_back() -> None:
    t = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)  # центр ≈ 2026-07-17 10:48
    hist = history(d2026_07_17=day(kp_values=[5.0] * 8))
    assert one("kp_recurrence_54d", t, hist)[0] == 5.0
    assert one("kp_recurrence_27d", t, hist) == (None, "missing")


# --- Величины из суточных строк ---


def test_quality_follows_row_qc_group() -> None:
    qc = {"cme": "degraded", "solar_wind": "ok"}
    hist = history(d2026_09_01=day(cme_count=3, sw_bt=5.0, qc=qc))
    assert one("cme_count_24h", datetime(2026, 9, 3, 6, 0, tzinfo=UTC), hist) == (3, "degraded")
    assert one("sw_bt", T, hist) == (5.0, "ok")


def test_null_field_is_missing() -> None:
    assert one("sw_bt", T, history(d2026_09_01=day())) == (None, "missing")


def test_zero_counts_are_values_not_gaps() -> None:
    hist = history(d2026_09_01=day(flare_counts={"B": 0, "C": 0, "M": 0, "X": 0}, cme_count=0))
    assert one("flares_x_24h", T, hist) == (0, "ok")
    assert one("flare_max_flux_24h", T, hist) == (None, "missing")  # вспышек не было


def test_flare_flux_from_class() -> None:
    hist = history(d2026_09_01=day(flare_max_class="M3.1"))
    assert one("flare_max_flux_24h", T, hist)[0] == pytest.approx(3.1e-5)
    hist = history(d2026_09_01=day(flare_max_class="X5.8"))
    assert one("flare_max_flux_24h", T, hist)[0] == pytest.approx(5.8e-4)


def test_mcintosh_max_follows_class_order() -> None:
    summary = {"mcintosh_z": {"B": 1, "H": 2, "D": 1, "F": 1}, "growth": {}}
    hist = history(d2026_09_01=day(ar_summary=summary))
    assert one("ar_max_mcintosh_z", T, hist)[0] == "H"
    hist = history(d2026_09_01=day(ar_summary={"mcintosh_z": {}, "growth": {}}))
    assert one("ar_max_mcintosh_z", T, hist) == (None, "missing")


def test_new_region_count_is_zero_when_none_are_new() -> None:
    hist = history(d2026_09_01=day(ar_summary={"mcintosh_z": {}, "growth": {"continuing": 4}}))
    assert one("ar_new_count", T, hist) == (0, "ok")
    hist = history(d2026_09_01=day(ar_summary={"mcintosh_z": {}, "growth": {"new": 3}}))
    assert one("ar_new_count", T, hist)[0] == 3


def test_sw_speed_trend_is_difference_of_two_days() -> None:
    hist = history(d2026_08_31=day(sw_speed_avg=350.0), d2026_09_01=day(sw_speed_avg=400.0))
    assert one("sw_speed_trend_24h", T, hist)[0] == 50.0


def test_sw_speed_trend_needs_both_days() -> None:
    hist = history(d2026_09_01=day(sw_speed_avg=400.0))
    assert one("sw_speed_trend_24h", T, hist) == (None, "missing")


# --- Сутки с последней вспышки ---


def _flares(m: int = 0, x: int = 0) -> dict[str, Any]:
    return day(flare_counts={"B": 0, "C": 0, "M": m, "X": x})


def test_days_since_last_m_counts_days_back_from_last_complete_day() -> None:
    hist = history(
        d2026_08_29=_flares(m=1),
        d2026_08_30=_flares(),
        d2026_08_31=_flares(),
        d2026_09_01=_flares(),
    )
    assert one("days_since_last_m", T, hist) == (3, "ok")


def test_days_since_last_m_is_zero_if_flare_was_in_last_day() -> None:
    assert one("days_since_last_m", T, history(d2026_09_01=_flares(m=2))) == (0, "ok")


def test_x_flare_counts_as_m_or_stronger() -> None:
    hist = history(d2026_08_31=_flares(x=1), d2026_09_01=_flares())
    assert one("days_since_last_m", T, hist)[0] == 1
    assert one("days_since_last_x", T, hist)[0] == 1


def test_m_flare_is_not_x() -> None:
    hist = history(d2026_09_01=_flares(m=3))
    assert one("days_since_last_x", T, hist) == (None, "missing")


def test_days_since_last_is_degraded_over_a_gap() -> None:
    hist = history(d2026_08_29=_flares(m=1), d2026_09_01=_flares())  # 08-30 и 08-31 нет
    assert one("days_since_last_m", T, hist) == (3, "degraded")


# --- Календарь и цикл ---


def test_day_of_year_uses_t_itself() -> None:
    assert one("doy", T, {}) == (245, "ok")
    assert one("doy", datetime(2024, 12, 31, 23, 0, tzinfo=UTC), {}) == (366, "ok")


def _ar_days(n: int, count: int, area: int) -> dict[date, dict[str, Any]]:
    last = date(2026, 9, 1)
    return {last - timedelta(days=i): day(ar_count=count, ar_total_area=area) for i in range(n)}


def test_cycle_proxy_is_one_at_reference_activity() -> None:
    value, quality = one("cycle_proxy", T, _ar_days(27, 15, 2000))
    assert value == pytest.approx(1.0)
    assert quality == "ok"


def test_cycle_proxy_is_between_zero_and_one_and_capped() -> None:
    assert one("cycle_proxy", T, _ar_days(27, 100, 99999))[0] == pytest.approx(1.0)
    value, _ = one("cycle_proxy", T, _ar_days(27, 3, 400))
    assert value == pytest.approx((3 / 15 + 400 / 2000) / 2)


def test_cycle_proxy_short_history_is_degraded_and_too_short_is_missing() -> None:
    assert one("cycle_proxy", T, _ar_days(10, 15, 2000))[1] == "degraded"
    assert one("cycle_proxy", T, _ar_days(5, 15, 2000)) == (None, "missing")


# --- Прогноз SWPC ---


def _forecast(hour: int, *grid: float, day_: int = 2) -> SwpcForecast:
    return SwpcForecast(datetime(2026, 9, day_, hour, 0, tzinfo=UTC), tuple(grid))


@pytest.mark.parametrize(
    ("max_kp", "level"),
    [(3.0, 0), (4.67, 0), (5.0, 1), (5.67, 1), (6.0, 2), (7.33, 3), (8.0, 4), (9.0, 5)],
)
def test_swpc_g_scale_from_kp_grid(max_kp: float, level: int) -> None:
    forecast = _forecast(0, 2.0, max_kp, 1.0)
    assert one("swpc_g_scale", T, {}, forecasts=[forecast]) == (level, "ok")


def test_swpc_g_scale_without_grid_is_missing_not_zero() -> None:
    assert one("swpc_g_scale", T, {}, forecasts=[_forecast(0)]) == (None, "missing")
    assert one("swpc_g_scale", T, {}) == (None, "missing")


def test_swpc_uses_latest_forecast_issued_not_after_t() -> None:
    forecasts = [
        _forecast(0, 6.0, day_=1),  # старый
        _forecast(3, 5.0),  # последний до t = 06:00
        _forecast(9, 9.0),  # выпущен после t
    ]
    assert one("swpc_g_scale", T, {}, forecasts=forecasts)[0] == 1


def test_swpc_forecast_requires_timezone() -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        SwpcForecast(datetime(2026, 9, 2, 0, 0), (5.0,))


def test_swpc_r_scale_is_never_filled_from_unreliable_text() -> None:
    assert one("swpc_r_scale", T, {}, forecasts=[_forecast(0, 9.0)]) == (None, "missing")


# --- Реальные снимки ---


def _real_history(fixture: str, d: date) -> dict[date, dict[str, Any]]:
    return {d: extract_observation_daily(load_fixture(fixture), d=d, raw_s3_key="k")}


def test_storm_day_features() -> None:
    d = date(2024, 5, 11)
    t = datetime(2024, 5, 13, 0, 0, tzinfo=UTC)  # все суточные признаки смотрят на 05-11
    row = compute_features(t, _real_history(STORM_DATA, d), SPECS)
    values = row.values
    assert values["flares_x_24h"] == 2
    assert values["flares_m_24h"] == 5
    assert values["flare_max_flux_24h"] == pytest.approx(5.8e-4)
    assert values["ar_has_delta"] is True
    assert values["ar_max_mcintosh_z"] == "H"
    assert values["cme_earth_directed"] == 2
    assert values["cme_direction_unknown"] == 1
    assert values["cme_earth_directed_speed_max_24h"] == 1263.0
    assert values["sw_sector"] == "toward"
    assert values["dst_min_1d"] == -406.0
    assert values["days_since_last_x"] == 0
    assert values["doy"] == 134


def test_quiet_day_features() -> None:
    d = date(2026, 9, 1)
    t = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    values = compute_features(t, _real_history(QUIET_DATA, d), SPECS).values
    assert values["flares_m_24h"] == 0
    assert values["flare_max_flux_24h"] == pytest.approx(5.7e-6)
    assert values["ar_has_delta"] is False
    assert values["cme_earth_directed"] == 0
    assert values["cme_earth_directed_speed_max_24h"] is None
    assert values["days_since_last_m"] is None
