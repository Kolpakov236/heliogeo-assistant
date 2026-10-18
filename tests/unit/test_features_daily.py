"""Суточные наблюдения: разбор ответа SWX API в колонки observation_daily."""

import math
from datetime import date
from typing import Any

import pytest

from heliogeo.features.daily import extract_observation_daily
from heliogeo.storage.models import ObservationDailyRecord
from tests.mocks.observation import make_observation_daily, make_storm_observation_daily
from tests.mocks.swx import QUIET_DATA, STORM_DATA, load_fixture

RAW_KEY = "swx/data/2026/09/01/1200_24h_nolive_0123456789ab.json"
DAY = date(2026, 9, 1)

# Поля, которые мок заполняет из того же снимка. kp_source, sw_source, dst_source и qc в моках
# выставлены не везде, поэтому проверяются отдельно.
FROM_SNAPSHOT = (
    "kp_max",
    "kp_values",
    "dst_min",
    "sw_speed_min",
    "sw_speed_avg",
    "sw_speed_max",
    "sw_bt",
    "sw_bz_min",
    "sw_density_avg",
    "sw_sector",
    "flare_max_class",
    "flare_counts",
    "ar_count",
    "ar_total_area",
    "ar_has_delta",
    "ar_summary",
    "cme_count",
    "cme_earth_directed",
    "cme_direction_unknown",
    "cme_speed_max",
    "cme_width_max",
    "cme_earth_directed_speed_max",
)


def _extract(data: dict[str, Any]) -> dict[str, Any]:
    return extract_observation_daily(data, d=DAY, raw_s3_key=RAW_KEY)


@pytest.mark.parametrize(
    ("fixture", "make_mock"),
    [(QUIET_DATA, make_observation_daily), (STORM_DATA, make_storm_observation_daily)],
    ids=["quiet", "storm"],
)
def test_matches_mock_built_from_same_snapshot(fixture: str, make_mock: Any) -> None:
    mock = make_mock()
    row = extract_observation_daily(load_fixture(fixture), d=mock.d, raw_s3_key=mock.raw_s3_key)
    for name in FROM_SNAPSHOT:
        assert row[name] == getattr(mock, name), name


def test_row_is_accepted_by_the_orm_model() -> None:
    row = _extract(load_fixture(QUIET_DATA))
    record = ObservationDailyRecord(**row)
    assert record.d == DAY
    assert record.raw_s3_key == RAW_KEY
    assert record.kp_source == "gfz"
    assert record.sw_source == "omni_hro_1min"


def test_kp_comes_from_geomag_not_from_solar_wind() -> None:
    """В solar_wind.kp_obs 24 значения: взять их значит нарушить kp_values_len."""
    row = _extract(load_fixture(QUIET_DATA))
    assert len(row["kp_values"]) == 8


def test_cme_direction_unknown_is_not_counted_as_away() -> None:
    row = _extract(load_fixture(STORM_DATA))
    assert row["cme_count"] == 5
    assert row["cme_earth_directed"] == 2
    assert row["cme_direction_unknown"] == 1
    assert row["cme_earth_directed"] + row["cme_direction_unknown"] <= row["cme_count"]


def test_storm_has_delta_region_and_x_flares() -> None:
    row = _extract(load_fixture(STORM_DATA))
    assert row["ar_has_delta"] is True
    assert row["flare_counts"]["X"] == 2
    assert row["flare_max_class"] == "X5.8"


def test_qc_statuses_for_quiet_day() -> None:
    qc = _extract(load_fixture(QUIET_DATA))["qc"]
    assert qc == {
        "kp": "ok",
        "dst": "ok",
        "solar_wind": "degraded",  # OMNI, не L1
        "flares": "ok",
        "active_regions": "ok",
        "cme": "degraded",  # у одного выброса направление не определено
    }


def test_empty_response_gives_missing_everywhere() -> None:
    row = _extract({})
    assert row["d"] == DAY
    assert row["raw_s3_key"] == RAW_KEY
    assert set(row["qc"].values()) == {"missing"}
    values = {k: v for k, v in row.items() if k not in {"d", "qc", "raw_s3_key"}}
    assert all(value is None for value in values.values())
    ObservationDailyRecord(**row)


def test_day_without_events_is_zero_not_missing() -> None:
    row = _extract({"flares": [], "cmes": [], "active_regions": []})
    assert row["flare_counts"] == {"B": 0, "C": 0, "M": 0, "X": 0}
    assert row["flare_max_class"] is None
    assert row["cme_count"] == 0
    assert row["cme_speed_max"] is None
    assert row["ar_count"] == 0
    assert row["ar_total_area"] == 0
    assert row["ar_has_delta"] is False
    assert row["ar_summary"] == {"mcintosh_z": {}, "growth": {}}
    assert row["qc"]["flares"] == row["qc"]["cme"] == row["qc"]["active_regions"] == "ok"


def test_flare_max_class_orders_letter_before_magnitude() -> None:
    data = {"flares": [{"class": "C9.9"}, {"class": "M1.0"}, {"class": "B9.9"}, {"class": "A2.0"}]}
    row = _extract(data)
    assert row["flare_max_class"] == "M1.0"
    assert row["flare_counts"] == {"B": 1, "C": 1, "M": 1, "X": 0}  # A в счётчик не входит


def test_flare_with_unreadable_class_degrades_quality() -> None:
    row = _extract({"flares": [{"class": "C1.0"}, {"class": "??"}, {"class": None}, {}]})
    assert row["flare_counts"]["C"] == 1
    assert row["flare_max_class"] == "C1.0"
    assert row["qc"]["flares"] == "degraded"


def test_kp_with_more_than_eight_values_is_rejected() -> None:
    row = _extract({"geomag": {"kp_obs": {"values": [1.0] * 24, "max": 1.0, "source": "gfz"}}})
    assert row["kp_max"] is None
    assert row["kp_values"] is None
    assert row["qc"]["kp"] == "invalid"
    ObservationDailyRecord(**row)


def test_kp_max_falls_back_to_values_and_short_day_is_degraded() -> None:
    row = _extract({"geomag": {"kp_obs": {"values": [1.0, 3.0, 2.0]}}})
    assert row["kp_max"] == 3.0
    assert row["kp_source"] is None
    assert row["qc"]["kp"] == "degraded"


def test_dst_falls_back_to_solar_wind_minimum() -> None:
    row = _extract({"solar_wind": {"dst_obs": {"min": -40}}})
    assert row["dst_min"] == -40.0
    assert row["dst_source"] is None
    assert row["qc"]["dst"] == "ok"


def test_unknown_sector_is_dropped() -> None:
    row = _extract({"solar_wind": {"speed": {"avg": 400.0}, "imf": {"sector": "sideways"}}})
    assert row["sw_sector"] is None
    assert row["sw_speed_avg"] == 400.0


def test_rtsw_source_is_ok() -> None:
    row = _extract({"solar_wind": {"speed": {"avg": 400.0}, "source": "rtsw"}})
    assert row["qc"]["solar_wind"] == "ok"


@pytest.mark.parametrize("bad", [True, "5", None, math.nan, math.inf, [1], {"v": 1}])
def test_non_numbers_become_none(bad: Any) -> None:
    row = _extract({"solar_wind": {"speed": {"min": bad, "avg": 400.0, "max": bad}}})
    assert row["sw_speed_min"] is None
    assert row["sw_speed_max"] is None
    assert row["sw_speed_avg"] == 400.0


def test_cme_with_missing_speed_is_counted_but_not_measured() -> None:
    row = _extract({"cmes": [{"earth_directed": True}, {"speed": 500, "earth_directed": False}]})
    assert row["cme_count"] == 2
    assert row["cme_speed_max"] == 500.0
    assert row["cme_earth_directed_speed_max"] is None
    assert row["cme_earth_directed"] == 1


def test_region_with_unknown_mcintosh_is_not_in_summary() -> None:
    data = {
        "active_regions": [
            {"mcintosh": "Cro", "area": 10, "growth": {"status": "new"}},
            {"mcintosh": "Zxx", "area": None},
            {"mcintosh": None, "magnetic": "Beta-Gamma-Delta"},
        ]
    }
    row = _extract(data)
    assert row["ar_count"] == 3
    assert row["ar_total_area"] == 10
    assert row["ar_has_delta"] is True
    assert row["ar_summary"] == {"mcintosh_z": {"C": 1}, "growth": {"new": 1}}


def test_empty_raw_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="raw_s3_key"):
        extract_observation_daily({}, d=DAY, raw_s3_key="  ")


def test_input_is_not_modified() -> None:
    data = load_fixture(STORM_DATA)
    before = load_fixture(STORM_DATA)
    _extract(data)
    assert data == before
