"""Проверка мок-фабрик observation_daily: данные согласованы и совпадают с реальными снимками."""

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from heliogeo.storage.models import ObservationDailyRecord
from tests.mocks.observation import (
    make_empty_observation_daily,
    make_observation_daily,
    make_storm_observation_daily,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FLARE_ORDER = "ABCMX"


def _snapshot(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"swx_snapshot_{name}.json").read_text(encoding="utf-8"))


def _flare_counts(flares: list[dict[str, Any]]) -> dict[str, int]:
    letters = Counter(f["class"][0] for f in flares)
    return {letter: letters.get(letter, 0) for letter in "BCMX"}


def _max_flare(flares: list[dict[str, Any]]) -> str:
    return max((f["class"] for f in flares), key=lambda c: (FLARE_ORDER.index(c[0]), float(c[1:])))


@pytest.mark.parametrize(
    ("factory", "snapshot"),
    [
        (make_observation_daily, "2026-09-01"),
        (make_storm_observation_daily, "2024-05-11"),
    ],
    ids=["quiet", "storm"],
)
def test_mock_matches_the_real_snapshot(factory: Any, snapshot: str) -> None:
    """Значения мока — не выдуманные: считаются из того же снимка, что и тесты экстрактора."""
    row: ObservationDailyRecord = factory()
    data = _snapshot(snapshot)
    wind, geomag = data["solar_wind"], data["geomag"]
    regions, cmes = data["active_regions"], data["cmes"]

    assert row.kp_values == geomag["kp_obs"]["values"]
    assert row.kp_max == geomag["kp_obs"]["max"]
    assert row.dst_min == geomag["dst_min_day"]
    assert (row.sw_speed_min, row.sw_speed_avg, row.sw_speed_max) == (
        wind["speed"]["min"],
        wind["speed"]["avg"],
        wind["speed"]["max"],
    )
    assert row.sw_bt == wind["imf"]["bt"]
    assert row.sw_bz_min == wind["imf"]["bz_min"]
    assert row.sw_density_avg == wind["density"]["avg"]
    assert row.sw_sector == wind["imf"]["sector"]
    assert row.flare_counts == _flare_counts(data["flares"])
    assert row.flare_max_class == _max_flare(data["flares"])
    assert row.ar_count == len(regions)
    assert row.ar_total_area == sum(r["area"] for r in regions)
    assert row.ar_has_delta == any("Delta" in r["magnetic"] for r in regions)
    assert row.ar_summary is not None
    assert row.ar_summary["growth"] == dict(Counter(r["growth"]["status"] for r in regions))
    assert row.cme_count == len(cmes)
    assert row.cme_earth_directed == sum(c["earth_directed"] is True for c in cmes)
    assert row.cme_direction_unknown == sum(c["earth_directed"] is None for c in cmes)
    assert row.cme_speed_max == max(c["speed"] for c in cmes)
    assert row.cme_width_max == max(c["width"] for c in cmes)
    directed = [c["speed"] for c in cmes if c["earth_directed"] is True]
    assert row.cme_earth_directed_speed_max == (max(directed) if directed else None)


@pytest.mark.parametrize("factory", [make_observation_daily, make_storm_observation_daily])
def test_mock_is_internally_consistent(factory: Any) -> None:
    row: ObservationDailyRecord = factory()
    assert row.kp_values is not None and len(row.kp_values) == 8
    assert row.kp_max == max(row.kp_values)
    assert row.sw_speed_min is not None and row.sw_speed_avg is not None
    assert row.sw_speed_max is not None
    assert row.sw_speed_min <= row.sw_speed_avg <= row.sw_speed_max
    assert row.cme_earth_directed is not None and row.cme_direction_unknown is not None
    assert row.cme_count is not None
    assert row.cme_earth_directed + row.cme_direction_unknown <= row.cme_count
    assert row.ar_summary is not None
    assert sum(row.ar_summary["mcintosh_z"].values()) == row.ar_count
    assert row.raw_s3_key.startswith(f"swx/data/{row.d:%Y/%m/%d}/")


def test_quiet_day_has_no_earth_directed_speed() -> None:
    """Нет выбросов к Земле — скорость NULL, а не 0 км/с."""
    row = make_observation_daily()
    assert row.cme_earth_directed == 0
    assert row.cme_earth_directed_speed_max is None


def test_overrides_win_over_defaults() -> None:
    row = make_observation_daily(kp_max=5.0, d=date(2026, 9, 3), sw_sector=None)
    assert (row.kp_max, row.d, row.sw_sector) == (5.0, date(2026, 9, 3), None)
    assert row.sw_speed_avg == 398.93  # остальное по умолчанию


def test_empty_day_has_only_date_and_raw_key() -> None:
    row = make_empty_observation_daily()
    assert row.d == date(2026, 9, 2)
    assert row.raw_s3_key == "swx/data/2026/09/02/0000_24h_nolive_000000000000.json"
    nullable = [
        column.name
        for column in ObservationDailyRecord.__table__.columns
        if column.name not in {"d", "raw_s3_key", "ingested_at"}
    ]
    assert all(getattr(row, name) is None for name in nullable)


def test_empty_day_accepts_another_date() -> None:
    assert make_empty_observation_daily(date(2026, 12, 31)).raw_s3_key.startswith(
        "swx/data/2026/12/31/"
    )
