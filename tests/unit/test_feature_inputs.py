"""Откуда в L1 берутся входы каждого признака Feature Store.

Для каждого признака из ``contracts/features/specs.yaml`` записано, из каких столбцов L1 он
считается. Тест падает, если в спецификацию добавили признак, для которого не указан вход, или если
указан столбец, которого нет в схеме L1: так состав озера и состав признаков не расходятся молча.
Признаки, входы которых сегодня всегда пусты, перечислены явно (``BLOCKED``).
"""

from pathlib import Path

import yaml

from heliogeo.extractors.swx.normalize import normalize_response
from heliogeo.schema.l1 import L1_SCHEMAS
from tests.mocks.swx import (
    QUIET_DATA,
    QUIET_END_OF_DAY,
    STORM_DATA,
    STORM_END_OF_DAY,
    load_fixture,
    make_data_response,
)

SPECS = Path(__file__).resolve().parents[2] / "contracts" / "features" / "specs.yaml"

# Признак -> (сущность, столбцы). «История» означает, что нужны разделы за несколько прошлых суток
# (читаются ``heliogeo.storage.lake.read_range``).
INPUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "kp_cur": ("geomag_obs", ("kp_3h",)),
    "kp_max_1d": ("geomag_obs", ("kp_max", "kp_3h")),
    "kp_mean_1d": ("geomag_obs", ("kp_3h",)),
    "kp_max_3d": ("geomag_obs", ("kp_max",)),  # история 3 суток
    "kp_mean_3d": ("geomag_obs", ("kp_3h",)),  # история 3 суток
    "kp_max_7d": ("geomag_obs", ("kp_max",)),  # история 7 суток
    "kp_mean_7d": ("geomag_obs", ("kp_3h",)),  # история 7 суток
    "kp_trend_3d": ("geomag_obs", ("kp_3h",)),  # история 3 суток
    "dst_min_1d": ("geomag_obs", ("dst_min_nt",)),
    "kp_recurrence_27d": ("geomag_obs", ("kp_max",)),  # история 27 суток
    "kp_recurrence_54d": ("geomag_obs", ("kp_max",)),  # история 54 суток
    "sw_speed_min": ("solar_wind", ("speed_min_km_s",)),
    "sw_speed_avg": ("solar_wind", ("speed_avg_km_s",)),
    "sw_speed_max": ("solar_wind", ("speed_max_km_s",)),
    "sw_density_avg": ("solar_wind", ("density_avg_cm3",)),
    "sw_bt": ("solar_wind", ("bt_nt", "bt_avg_nt")),
    "sw_bz_min": ("solar_wind", ("bz_min_nt",)),
    "sw_sector": ("solar_wind", ("sector",)),
    "sw_speed_trend_24h": ("solar_wind", ("speed_avg_km_s",)),  # история 2 суток
    "ar_count": ("active_regions", ("noaa_number", "observed_date")),
    "ar_total_area": ("active_regions", ("area_msh",)),
    "ar_has_delta": ("active_regions", ("magnetic",)),
    "ar_max_mcintosh_z": ("active_regions", ("mcintosh",)),
    "ar_new_count": ("active_regions", ("noaa_number", "growth_status")),  # история сутки
    "flares_c_24h": ("flares", ("flare_class", "max_utc")),
    "flares_m_24h": ("flares", ("flare_class", "max_utc")),
    "flares_x_24h": ("flares", ("flare_class", "max_utc")),
    "flare_max_flux_24h": ("flares", ("flare_class",)),
    "days_since_last_m": ("flares", ("flare_class", "max_utc")),  # история
    "days_since_last_x": ("flares", ("flare_class", "max_utc")),  # история
    "cme_count_24h": ("cmes", ("cme_id", "time_utc")),
    "cme_earth_directed": ("cmes", ("earth_directed",)),
    "cme_direction_unknown": ("cmes", ("earth_directed",)),
    "cme_speed_max_24h": ("cmes", ("speed_km_s",)),
    "cme_width_max_24h": ("cmes", ("width_deg",)),
    "cme_earth_directed_speed_max_24h": ("cmes", ("earth_directed", "speed_km_s")),
    "doy": ("active_regions", ("observed_date",)),  # из даты раздела, столбец не нужен
    "cycle_proxy": ("active_regions", ("noaa_number", "area_msh")),
    "swpc_g_scale": ("forecast_ext", ("g_scale",)),  # плюс issued_utc для условия «не позже t»
    "swpc_r_scale": ("forecast_ext", ("r_scale",)),  # плюс issued_utc для условия «не позже t»
}

# Входы, которые сегодня всегда пусты: источник читает прогнозные шкалы с ошибкой, и L1 их не
# переносит (см. docs/data_sources.md). Снимется после нового разбора прогноза.
BLOCKED: dict[str, str] = {
    "swpc_g_scale": "g_scale: шкала считается по сетке Kp, которой в ответе источника нет",
    "swpc_r_scale": "r_scale: шкала R источником читается с ошибкой и не переносится",
}


def _spec_ids() -> list[str]:
    specs = yaml.safe_load(SPECS.read_text(encoding="utf-8"))
    return [feature["id"] for feature in specs["features"]]


def test_every_feature_of_the_spec_has_a_described_input_and_nothing_extra() -> None:
    assert sorted(INPUTS) == sorted(_spec_ids())


def test_every_input_column_exists_in_the_l1_schema() -> None:
    for feature, (entity, columns) in INPUTS.items():
        names = set(L1_SCHEMAS[entity].column_names)
        assert set(columns) <= names, feature


def test_blocked_features_are_exactly_the_ones_whose_inputs_are_empty_today() -> None:
    empty: set[str] = set()
    for name, at in ((QUIET_DATA, QUIET_END_OF_DAY), (STORM_DATA, STORM_END_OF_DAY)):
        response = make_data_response(load_fixture(name), at=at, no_live=True)
        rows = normalize_response(response, raw_key="k").rows
        for feature, (entity, columns) in INPUTS.items():
            if rows[entity] and all(row[c] is None for row in rows[entity] for c in columns):
                empty.add(feature)
    # уровни шкал дают пустые значения сегодня; у остальных признаков вход на фикстурах заполнен
    assert empty == set(BLOCKED)


def test_the_other_features_have_filled_inputs_on_a_reference_day() -> None:
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_END_OF_DAY, no_live=True)
    rows = normalize_response(response, raw_key="k").rows

    for feature, (entity, columns) in INPUTS.items():
        if feature in BLOCKED or feature == "doy":
            continue
        filled = any(row[c] is not None for row in rows[entity] for c in columns)
        assert filled, feature
