"""Тесты mapper: swx JSON → UnifiedPayload."""

import json
from pathlib import Path

import pytest

from heliogeo.extractors.swx.mapper import (
    _kp_dst_to_level,
    map_active_regions,
    map_cmes,
    map_flares,
    map_forecast,
    map_geospace,
    map_images,
    map_particles,
    map_solar_activity,
    map_solar_wind,
    map_technical_issues,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def snapshot_quiet() -> dict:
    """Спокойный день 2026-09-01."""
    path = FIXTURES_DIR / "swx_snapshot_2026-09-01.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot_storm() -> dict:
    """Бурный день 2024-05-11 (G5)."""
    path = FIXTURES_DIR / "swx_snapshot_2024-05-11.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- map_active_regions ---


def test_map_active_regions_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: 6 регионов."""
    regions = map_active_regions(snapshot_quiet["active_regions"])
    assert len(regions) == 6
    assert regions[0]["noaa_num"] == 4513
    assert regions[0]["location"] == "N05W88"
    assert regions[0]["mcintosh"] == "Cro"
    assert regions[0]["magnetic_class"] == "Beta"
    assert regions[0]["area"] == 10
    assert regions[0]["spots"] == 2
    assert regions[0]["growth"] == "continuing"


def test_map_active_regions_growth_new() -> None:
    """growth со статусом 'new' → 'new'."""
    rows = [
        {
            "noaa": 9999,
            "location": "N00E00",
            "area": 1,
            "spots": 1,
            "mcintosh": "Axx",
            "magnetic": "Alpha",
            "growth": {"status": "new"},
        }
    ]
    result = map_active_regions(rows)
    assert result[0]["growth"] == "new"


def test_map_active_regions_empty_growth() -> None:
    """growth отсутствует → None."""
    rows = [{"noaa": 1, "location": "N00E00", "growth": None}]
    result = map_active_regions(rows)
    assert result[0]["growth"] is None


def test_map_active_regions_empty_list() -> None:
    """Пустой список → пустой список."""
    assert map_active_regions([]) == []


# --- map_flares ---


def test_map_flares_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: 8 вспышек."""
    flares = map_flares(snapshot_quiet["flares"])
    assert len(flares) == 8
    # Первая вспышка B7.7
    assert flares[0]["class"] == "B7.7"
    assert flares[0]["time"] == "2026-09-01T07:36:00+00:00"
    assert flares[0]["ar"] is None
    assert flares[0]["radio_signatures"] == []


def test_map_flares_with_radio(snapshot_quiet: dict) -> None:
    """Вспышка с radio."""
    flares = map_flares(snapshot_quiet["flares"])
    # C5.7 с VII/2
    c57 = next(f for f in flares if f["class"] == "C5.7")
    assert c57["radio_signatures"] == ["VII/2"]


def test_map_flares_with_ar(snapshot_quiet: dict) -> None:
    """Вспышка с ar."""
    flares = map_flares(snapshot_quiet["flares"])
    b79 = next(f for f in flares if f["class"] == "B7.9")
    assert b79["ar"] == 4520


def test_map_flares_no_time() -> None:
    """Flare без time — пропускается."""
    rows = [{"class": "M1.0"}]  # нет begin/max
    result = map_flares(rows)
    assert result == []


# --- map_cmes ---


def test_map_cmes_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: 3 CME, все не к Земле."""
    cmes = map_cmes(snapshot_quiet["cmes"])
    assert len(cmes) == 3
    assert cmes[0]["speed"] == 253.0
    assert cmes[0]["width"] == 32.0
    assert cmes[0]["earth_directed"] is False


def test_map_cmes_null_earth_directed(snapshot_quiet: dict) -> None:
    """earth_directed=null → False."""
    cmes = map_cmes(snapshot_quiet["cmes"])
    # Третий CME имеет earth_directed: null
    assert cmes[2]["earth_directed"] is False


def test_map_cmes_null_speed() -> None:
    """speed отсутствует → 0.0."""
    rows = [{"time": "2026-01-01T00:00Z", "width": 100}]
    result = map_cmes(rows)
    assert result[0]["speed"] == 0.0


# --- map_solar_activity ---


def test_map_solar_activity_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: полная секция."""
    section = map_solar_activity(snapshot_quiet)
    assert len(section["active_regions"]) == 6
    assert len(section["flares_last_24h"]) == 8
    assert len(section["cme_list"]) == 3
    assert section["f107"] is None
    assert section["ssn"] is None


def test_map_solar_activity_storm(snapshot_storm: dict) -> None:
    """Бурный день: больше активных регионов."""
    section = map_solar_activity(snapshot_storm)
    assert len(section["active_regions"]) > 0


# --- map_particles ---


def test_map_particles_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: latest/peak = null, sep_flag=False."""
    particles = map_particles(snapshot_quiet["particles"])
    assert particles["e_2mev"] is None
    assert particles["p_10mev_peak"] is None
    assert particles["p_100mev_peak"] is None
    assert particles["sep_flag"] is False


def test_map_particles_storm(snapshot_storm: dict) -> None:
    """Бурный день: возможно SEP."""
    particles = map_particles(snapshot_storm["particles"])
    # Может быть True или False в зависимости от данных
    assert isinstance(particles["sep_flag"], bool)


def test_map_particles_empty() -> None:
    """Пустой particles → все None."""
    result = map_particles({})
    assert result["e_2mev"] is None
    assert result["sep_flag"] is False


# --- map_solar_wind ---


def test_map_solar_wind_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: speed, density, imf."""
    sw = map_solar_wind(snapshot_quiet["solar_wind"])
    assert sw["speed_min"] == 347.9
    assert sw["speed_avg"] == 398.93
    assert sw["speed_max"] == 449.6
    assert sw["density"] == 4.084  # среднее за окно; latest = 4.36 не берётся
    assert sw["bt"] == 5.17
    assert sw["bz_min"] == -5.71
    assert sw["bz_max"] == 5.24
    assert sw["phi"] == 111.9
    assert sw["temperature"] is None
    assert sw["source"] == "unknown"


def test_map_solar_wind_storm(snapshot_storm: dict) -> None:
    """Бурный день: выше скорость."""
    sw = map_solar_wind(snapshot_storm["solar_wind"])
    # В бурю скорость выше
    assert sw["speed_avg"] is not None
    assert sw["speed_avg"] > 400


def test_map_solar_wind_density_is_the_window_average(
    snapshot_quiet: dict, snapshot_storm: dict
) -> None:
    """density — среднее за окно, как и скорость; latest не берётся.

    В архиве latest бывает null (2024-05-11), и тогда значение меняло бы смысл от дня к дню.
    """
    assert map_solar_wind(snapshot_quiet["solar_wind"])["density"] == 4.084
    assert map_solar_wind(snapshot_storm["solar_wind"])["density"] == 15.647
    assert snapshot_storm["solar_wind"]["density"]["latest"] is None


def test_map_solar_wind_density_without_average_is_none() -> None:
    """Нет среднего — нет значения: latest его не подменяет."""
    assert map_solar_wind({"density": {"latest": 4.36}})["density"] is None
    assert map_solar_wind({"density": {}})["density"] is None
    assert map_solar_wind({})["density"] is None


def test_map_solar_wind_zero_density_average_is_kept() -> None:
    assert map_solar_wind({"density": {"avg": 0.0, "latest": 4.36}})["density"] == 0.0


def test_map_solar_wind_empty() -> None:
    """Пустой solar_wind → все None."""
    result = map_solar_wind({})
    assert result["speed_avg"] is None
    assert result["source"] == "unknown"


# --- map_geospace ---


def test_map_geospace_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: kp=2.333, dst=-9, level=quiet."""
    geo = map_geospace(snapshot_quiet["geomag"])
    assert geo["kp_observed"] == 2.333
    assert geo["dst_observed"] == -9
    assert geo["current_level"] == "quiet"
    assert geo["kp_fcst"] is None


def test_map_geospace_storm(snapshot_storm: dict) -> None:
    """Бурный день: high Kp, level=storm."""
    geo = map_geospace(snapshot_storm["geomag"])
    assert geo["kp_observed"] is not None
    assert geo["kp_observed"] >= 5
    assert geo["current_level"] == "storm"


def test_map_geospace_empty() -> None:
    """Пустой geomag → все None, level=quiet."""
    result = map_geospace({})
    assert result["kp_observed"] is None
    assert result["current_level"] == "quiet"


@pytest.mark.parametrize(
    "kp,dst,expected",
    [
        (2.0, -5, "quiet"),
        (4.0, -20, "active"),
        (5.0, -60, "storm"),
        (3.0, -30, "active"),
        (8.0, -200, "storm"),
        (1.0, 0, "quiet"),
    ],
)
def test_kp_dst_to_level(kp: float, dst: float, expected: str) -> None:
    """_kp_dst_to_level: различные комбинации."""
    assert _kp_dst_to_level(kp, dst) == expected


def test_kp_dst_to_level_none() -> None:
    """Оба None → quiet."""
    assert _kp_dst_to_level(None, None) == "quiet"


# --- map_forecast ---


def test_map_forecast_quiet(snapshot_quiet: dict) -> None:
    """Спокойный день: источник пишет g_scale=G1, но сетки Kp нет — шкалы G в payload нет."""
    assert snapshot_quiet["geomag"]["fcst"]["g_scale"] == "G1"
    assert snapshot_quiet["geomag"]["fcst"]["kp_forecast"] == []
    fc = map_forecast(snapshot_quiet["geomag"])
    assert fc["geomagnetic_g"] is None
    assert fc["flare_c_prob"] is None
    assert fc["drivers"] == []


def test_map_forecast_storm(snapshot_storm: dict) -> None:
    """Бурный день: сетки Kp в ответе нет, поле g_scale источника не используется."""
    assert snapshot_storm["geomag"]["fcst"]["g_scale"] == "G5"
    fc = map_forecast(snapshot_storm["geomag"])
    assert fc["geomagnetic_g"] is None


def test_map_forecast_empty() -> None:
    """Пустой → все None."""
    result = map_forecast({})
    assert result["geomagnetic_g"] is None


@pytest.mark.parametrize(
    "grid,expected",
    [
        ([1.67, 1.33, 2.0], None),
        ([4.67, 4.0, 3.67], None),  # 5- — ниже G1
        ([1.67, 5.0, 3.67], "G1"),
        ([2.0, 5.67, 3.0], "G1"),  # 6- — всё ещё G1
        ([3.0, 6.0], "G2"),
        ([7.33], "G3"),
        ([8.67], "G4"),  # 9- — G4
        ([9.0], "G5"),
    ],
)
def test_map_forecast_g_scale_comes_from_the_kp_grid(
    grid: list[float], expected: str | None
) -> None:
    """Шкала G — по максимуму числовой сетки Kp, а не по полю g_scale источника."""
    fc = map_forecast({"fcst": {"kp_forecast": grid, "g_scale": "G1"}})
    assert fc["geomagnetic_g"] == expected


def test_map_forecast_ignores_g_scale_of_the_source() -> None:
    """В тексте прогноза «No G1 … are expected», а разбор источника вернул G1."""
    assert map_forecast({"fcst": {"kp_forecast": [], "g_scale": "G1"}})["geomagnetic_g"] is None
    assert map_forecast({"fcst": {"g_scale": "G3"}})["geomagnetic_g"] is None


def test_map_forecast_skips_non_numeric_grid_values() -> None:
    fc = map_forecast({"fcst": {"kp_forecast": [None, "7.0", True, 6.0]}})
    assert fc["geomagnetic_g"] == "G2"


def test_map_geospace_kp_fcst_is_the_grid_maximum() -> None:
    """kp_fcst и шкала G считаются от одного и того же максимума сетки."""
    geo = map_geospace({"fcst": {"kp_forecast": [1.67, 5.0, 3.67]}})
    assert geo["kp_fcst"] == 5.0


# --- map_images ---


def test_map_images_returns_empty() -> None:
    """images — пустой dict (заполняется отдельно)."""
    assert map_images(None) == {}


# --- map_technical_issues ---


def test_technical_issues_empty_old_date() -> None:
    """Старая дата без частичных продуктов → проверяем отсутствие particle issue."""
    result = map_technical_issues(
        [],
        "rtsw",
        "2019-01-01",
        has_l1_rtsw=True,
        active_regions_growth="new",
    )

    assert result is not None
    assert "SGPS" not in result
    assert "MPS-HI" not in result


def test_technical_issues_sgps_only() -> None:
    """Дата после 2020-11-01 → есть предупреждение про SGPS."""
    result = map_technical_issues(
        [],
        "rtsw",
        "2026-09-01",
        has_l1_rtsw=True,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert "SGPS" in result
    assert "OMNI" not in result
    assert "omni_hro" not in result.lower()
    assert "omni2" not in result.lower()


def test_technical_issues_omni_hro() -> None:
    """OMNI HRO → предупреждение про subsolar bow shock nose."""
    result = map_technical_issues(
        [],
        "omni_hro",
        "2026-09-01",
        has_l1_rtsw=False,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert "omni_hro" in result
    assert "subsolar bow shock nose" in result
    assert "not raw DSCOVR/ACE" in result


@pytest.mark.parametrize(
    ("source", "caveat"),
    [
        ("omni_hro_1min", "subsolar bow shock nose"),
        ("omni2_hourly", "OMNI HRO 1-min not downloaded"),
    ],
)
def test_technical_issues_real_omni_source_values(source: str, caveat: str) -> None:
    """Источник отдаёт omni_hro_1min и omni2_hourly: оговорки не должны теряться."""
    result = map_technical_issues(
        [],
        source,
        "2026-09-01",
        has_l1_rtsw=False,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert caveat in result


def test_technical_issues_null_solar_wind_source_does_not_crash() -> None:
    """У части суток solar_wind.source приходит как null: источник неизвестен, а не «None»."""
    result = map_technical_issues(
        [],
        None,  # type: ignore[arg-type]
        "2026-01-15",
        has_l1_rtsw=False,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert "Solar wind source: unknown" in result
    assert "None" not in result


def test_technical_issues_l1_ace_no_omni_warning() -> None:
    """L1 ACE → нет предупреждения про OMNI bow shock."""
    result = map_technical_issues(
        [],
        "l1_ace",
        "2026-09-01",
        has_l1_rtsw=True,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert "l1_ace" in result
    assert "bow shock nose" not in result
    assert "not raw DSCOVR/ACE" not in result


def test_technical_issues_omni2() -> None:
    """OMNI2 → предупреждение про 1-hour history."""
    result = map_technical_issues(
        [],
        "omni2",
        "2026-09-01",
        has_l1_rtsw=False,
        forecast_available=True,
        discussion_available=True,
        active_regions_growth="empty",
    )

    assert result is not None
    assert "omni2" in result
    assert "1-hour" in result
    assert "OMNI HRO 1-min not downloaded" in result


def test_technical_issues_sgps() -> None:
    """Дата после 2020-11-01 → issue про SGPS."""
    result = map_technical_issues([], "rtsw", "2026-09-01")
    assert result is not None
    assert "SGPS" in result


def test_technical_issues_notes_passthrough() -> None:
    """notes передаются как есть."""
    result = map_technical_issues(["Test note"], "rtsw", "2020-01-01")
    assert result is not None
    assert "Test note" in result


def test_technical_issues_combined() -> None:
    """notes + omni + sgps вместе."""
    result = map_technical_issues(["Note 1"], "omni_hro_1min", "2026-09-01")
    assert result is not None
    assert "Note 1" in result
    assert "omni_hro_1min" in result
    assert "SGPS" in result
