"""Нормализация ответа ``/data`` в строки L1: правильные строки, None вместо подмены, отказ на неполных сутках."""

import copy
from datetime import UTC, date, datetime
from typing import Any

import pytest

from heliogeo.extractors.swx.normalize import L1Partition, NormalizeError, normalize_response
from heliogeo.schema.l1 import L1_SCHEMAS, validate_rows
from tests.mocks.swx import (
    QUIET_DATA,
    QUIET_END_OF_DAY,
    STORM_DATA,
    STORM_END_OF_DAY,
    STORM_NOON,
    load_fixture,
    make_data_response,
)

RAW_KEY = "live/2026-09-02/0000/data_ut_day_nolive.json"


def _partition(name: str = QUIET_DATA, at: datetime = QUIET_END_OF_DAY, **edit: Any) -> L1Partition:
    """Раздел из фикстуры на конец суток; ``edit`` — правки тела до разбора: {секция: значение}."""
    raw = load_fixture(name)
    raw.update(edit)
    response = make_data_response(raw, at=at, no_live=True)
    return normalize_response(response, raw_key=RAW_KEY)


@pytest.mark.parametrize(
    ("name", "at", "day", "counts"),
    [
        (QUIET_DATA, QUIET_END_OF_DAY, date(2026, 9, 1), (6, 8, 3)),
        (STORM_DATA, STORM_END_OF_DAY, date(2024, 5, 11), (7, 11, 5)),
    ],
)
def test_fixture_days_become_valid_partitions(
    name: str, at: datetime, day: date, counts: tuple[int, int, int]
) -> None:
    part = _partition(name, at)

    assert part.day == day
    assert set(part.rows) == set(L1_SCHEMAS)
    for entity, rows in part.rows.items():
        assert validate_rows(entity, list(rows)) == [], entity
    assert tuple(len(part.rows[e]) for e in ("active_regions", "flares", "cmes")) == counts
    for entity in ("particles", "solar_wind", "geomag_obs", "forecast_ext"):
        assert len(part.rows[entity]) == 1


def test_values_are_carried_over_with_units_in_the_names() -> None:
    part = _partition(STORM_DATA, STORM_END_OF_DAY)

    region = part.rows["active_regions"][1]
    assert (region["noaa_number"], region["area_msh"], region["spots"]) == (3664, 2400, 58)
    assert region["growth_area_delta_msh"] == 1310
    assert region["observed_date"] == date(2024, 5, 11)
    assert region["issued_utc"] == datetime(2024, 5, 11, 0, 30, tzinfo=UTC)
    flare = next(f for f in part.rows["flares"] if f["flare_class"] == "X5.8")
    assert flare["max_utc"] == datetime(2024, 5, 11, 1, 23, tzinfo=UTC)
    assert flare["active_region"] == 3664
    cme = part.rows["cmes"][0]
    assert (cme["cme_id"], cme["speed_km_s"], cme["earth_directed"]) == (
        "2024-05-11T01:36:00-CME-001",
        1263.0,
        True,
    )
    assert cme["time_utc"] == datetime(2024, 5, 11, 1, 36, tzinfo=UTC)
    wind = part.rows["solar_wind"][0]
    assert (wind["speed_avg_km_s"], wind["bz_min_nt"], wind["sector"]) == (781.61, -47.85, "toward")
    geomag = part.rows["geomag_obs"][0]
    assert len(geomag["kp_3h"]) == 8 and geomag["kp_max"] == 9.0
    assert (geomag["dst_latest_nt"], geomag["dst_min_nt"]) == (-167, -406)


def test_unknown_stays_none_and_is_not_replaced_by_a_default() -> None:
    part = _partition(STORM_DATA, STORM_END_OF_DAY)

    unknown_speed = [c for c in part.rows["cmes"] if c["cme_id"].startswith("2024-05-11T02:36")]
    assert unknown_speed and unknown_speed[0]["earth_directed"] is None
    assert unknown_speed[0]["lon_deg"] is None
    particles = part.rows["particles"][0]
    assert particles["electron_gt2mev_latest_pfu"] is None
    assert particles["proton_gt100mev_fluence_cm2_sr_day"] is None
    assert particles["sep_note"] is None
    assert particles["electron_gt2mev_fluence_cm2_sr_day"] == 2350000.0


def test_unreliable_forecast_scales_of_the_source_are_not_carried() -> None:
    """Источник отдаёт G5, S1, R3; это следствие ошибки разбора, а не прогноз."""
    row = _partition(STORM_DATA, STORM_END_OF_DAY).rows["forecast_ext"][0]

    assert (row["r_scale"], row["s_scale"], row["g_scale"]) == (None, None, None)
    assert row["issued_utc"] == datetime(2024, 5, 11, 12, 30, tzinfo=UTC)


def test_g_scale_is_taken_from_the_kp_grid_when_the_source_provides_it() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["geomag"]["fcst"]["kp_forecast"] = [2.0, 3.3, 6.0, 5.0]

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert part.rows["forecast_ext"][0]["g_scale"] == "G2"


def test_provenance_points_to_the_source_file_and_the_raw_object() -> None:
    part = _partition()

    for rows in part.rows.values():
        for row in rows:
            assert row["raw_key"] == RAW_KEY
    assert part.rows["active_regions"][0]["source_product"] == "srs"
    assert part.rows["active_regions"][0]["source_path"].endswith("20260901SRS.txt")
    assert part.rows["geomag_obs"][0]["source_product"] == "gfz_kp"
    assert part.rows["solar_wind"][0]["source_product"] == "omni_hro_1min"
    assert part.rows["solar_wind"][0]["source_path"].endswith("omni_min202609.asc")


def test_a_row_without_a_required_value_is_dropped_and_reported() -> None:
    raw = load_fixture(QUIET_DATA)
    del raw["flares"][0]["class"]
    del raw["flares"][1]["max"]

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert len(part.rows["flares"]) == len(raw["flares"]) - 2
    assert sum("flares[" in p and "отброшена" in p for p in part.problems) >= 2


def test_zero_in_the_source_becomes_none_with_a_note() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["cmes"][0]["speed"] = 0
    raw["solar_wind"]["speed"]["avg"] = 0.0

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert part.rows["cmes"][0]["speed_km_s"] is None
    assert part.rows["solar_wind"][0]["speed_avg_km_s"] is None
    assert any("cmes[0].speed_km_s: ноль в источнике заменён на None" in p for p in part.problems)
    assert any("solar_wind[0].speed_avg_km_s" in p for p in part.problems)


def test_empty_strings_become_none() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["cmes"][0]["note"] = ""
    raw["active_regions"][0]["mcintosh"] = "  "

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert part.rows["cmes"][0]["note"] is None
    assert part.rows["active_regions"][0]["mcintosh"] is None


def test_a_value_of_the_wrong_type_is_reported_and_becomes_none() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["cmes"][0]["speed"] = "fast"
    raw["cmes"][0]["earth_directed"] = "yes"

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert part.rows["cmes"][0]["speed_km_s"] is None
    assert part.rows["cmes"][0]["earth_directed"] is None
    assert sum("cmes[0]" in p for p in part.problems) == 2


def test_a_repeated_key_keeps_the_first_row() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["cmes"].append(copy.deepcopy(raw["cmes"][0]))

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert len(part.rows["cmes"]) == len(raw["cmes"]) - 1
    assert any("ключ" in p and "уже есть" in p for p in part.problems)


def test_absent_sections_give_empty_entities_not_empty_rows() -> None:
    raw = load_fixture(QUIET_DATA)
    for key in ("particles", "solar_wind", "geomag"):
        del raw[key]

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    for entity in ("particles", "solar_wind", "geomag_obs", "forecast_ext"):
        assert part.rows[entity] == ()


def test_a_wind_series_without_a_name_gives_no_row() -> None:
    raw = load_fixture(QUIET_DATA)
    del raw["solar_wind"]["source"]

    part = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    )

    assert part.rows["solar_wind"] == ()
    assert any("не назван ряд ветра" in p for p in part.problems)


def test_a_wind_series_without_a_file_keeps_the_row_with_no_path() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["solar_wind"]["source"] = "rtsw"

    row = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    ).rows["solar_wind"][0]

    assert (row["source_product"], row["source_path"]) == ("rtsw", None)


def test_the_hourly_wind_series_maps_to_the_omni2_product() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["solar_wind"]["source"] = "omni2_hourly"

    row = normalize_response(
        make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
    ).rows["solar_wind"][0]

    assert row["source_product"] == "omni2"
    assert row["source_path"].endswith("omni2_2026.dat")


def test_a_response_received_before_the_end_of_the_day_is_refused() -> None:
    """В озеро не должны лечь сутки, у которых отсечены события и неполны агрегаты."""
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_NOON, no_live=True)

    with pytest.raises(NormalizeError, match="раньше конца суток"):
        normalize_response(response, raw_key=RAW_KEY)


@pytest.mark.parametrize("window", ["15m", "1h", "1d"])
def test_only_the_calendar_day_window_gives_a_partition(window: str) -> None:
    response = make_data_response(at=QUIET_END_OF_DAY, window=window, no_live=True)

    with pytest.raises(NormalizeError, match="суточного окна"):
        normalize_response(response, raw_key=RAW_KEY)


def test_a_response_without_a_parsed_window_is_refused() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["window"] = {"spec": "ut_day"}

    with pytest.raises(NormalizeError, match="границы суток"):
        normalize_response(
            make_data_response(raw, at=QUIET_END_OF_DAY, no_live=True), raw_key=RAW_KEY
        )


def test_live_data_is_flagged_in_the_problems() -> None:
    response = make_data_response(at=QUIET_END_OF_DAY, no_live=False)

    part = normalize_response(response, raw_key=RAW_KEY)

    assert any("no_live=False" in p for p in part.problems)


def test_a_clean_fixture_gives_no_problems() -> None:
    assert _partition().problems == ()
