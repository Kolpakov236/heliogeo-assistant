"""Эталонные строки L1: по одной на сущность, значения взяты из снимка 2024-05-11.

Пример важнее схемы: он показывает, как выглядит правильная строка. Тот, кто пишет L1, сверяет
свой результат с этими строками и запускает ``validate_rows`` на своих данных.
"""

from datetime import UTC, date, datetime
from typing import Any

from heliogeo.schema.l1 import L1_SCHEMAS

RAW_KEY = "live/2024-05-11/1200/data_ut_day_nolive.json"


def _at(hour: int, minute: int) -> datetime:
    return datetime(2024, 5, 11, hour, minute, tzinfo=UTC)


def _provenance(product: str, path: str) -> dict[str, Any]:
    return {"source_product": product, "source_path": path, "raw_key": RAW_KEY}


_DATA = "/opt/swx/data"

_EXAMPLES: dict[str, dict[str, Any]] = {
    "active_regions": {
        "noaa_number": 3664,
        "observed_date": date(2024, 5, 11),
        "issued_utc": _at(0, 30),
        "location": "S17W48",
        "carrington_lon_deg": 347,
        "area_msh": 2400,
        "extent_deg": 20,
        "spots": 58,
        "mcintosh": "Fkc",
        "magnetic": "Beta-Gamma-Delta",
        "growth_status": "continuing",
        "growth_area_delta_msh": 1310,
        "growth_spots_delta": -23,
        **_provenance("srs", f"{_DATA}/warehouse/srs/2024/20240511SRS.txt"),
    },
    "flares": {
        "begin_utc": _at(1, 10),
        "max_utc": _at(1, 23),
        "end_utc": _at(1, 39),
        "flare_class": "X5.8",
        "active_region": 3664,
        "location": None,
        "observatory": "G16",
        "radio": ["CTM", "VI", "IV", "II"],
        **_provenance("events", f"{_DATA}/warehouse/events/2024/20240511events.txt"),
    },
    "cmes": {
        "cme_id": "2024-05-11T01:36:00-CME-001",
        "time_utc": _at(1, 36),
        "speed_km_s": 1263.0,
        "width_deg": 102.0,
        "half_angle_deg": 51.0,
        "lat_deg": 0.0,
        "lon_deg": 51.0,
        "cme_type": "O",
        "earth_directed": True,
        "catalog": "M2M_CATALOG",
        "source_location": "S17W47",
        "active_region": 13664,
        "note": None,
        **_provenance("donki_cme", f"{_DATA}/donki/cme_2024-05.json"),
    },
    "particles": {
        "electron_gt2mev_fluence_cm2_sr_day": 2350000.0,
        "electron_gt2mev_latest_pfu": None,
        "electron_gt2mev_peak_pfu": None,
        "electron_gt2mev_background_pfu": None,
        "electron_gt2mev_alert": None,
        "proton_gt10mev_fluence_cm2_sr_day": 5040000.0,
        "proton_gt10mev_latest_pfu": None,
        "proton_gt10mev_peak_pfu": None,
        "proton_gt10mev_background_pfu": None,
        "proton_gt100mev_fluence_cm2_sr_day": None,
        "proton_gt100mev_latest_pfu": None,
        "proton_gt100mev_peak_pfu": None,
        "proton_gt100mev_background_pfu": None,
        "sep_s_scale": None,
        "sep_gt10_event": True,
        "sep_gt100_event": False,
        "sep_alert_flags": [],
        "sep_note": None,
        **_provenance("dayind", f"{_DATA}/warehouse/dayind/2024/20240511dayind.txt"),
    },
    "solar_wind": {
        "n_minutes": 1440,
        "speed_min_km_s": 655.3,
        "speed_avg_km_s": 781.61,
        "speed_max_km_s": 955.0,
        "density_latest_cm3": None,
        "density_avg_cm3": 15.647,
        "bt_nt": 7.7,
        "bt_avg_nt": 25.749,
        "bz_min_nt": -47.85,
        "bz_max_nt": 18.14,
        "bz_latest_nt": 1.29,
        "phi_deg": 58.1,
        "sector": "toward",
        "wind_source": "omni_hro_1min",
        **_provenance("omni_hro_1min", f"{_DATA}/omni_min/omni_min202405.asc"),
    },
    "geomag_obs": {
        "kp_3h": [9.0, 8.333, 8.333, 9.0, 8.667, 8.333, 7.667, 7.667],
        "kp_max": 9.0,
        "kp_est_now": None,
        "kp_source": "gfz",
        "dst_latest_nt": -167.0,
        "dst_min_nt": -406.0,
        **_provenance("gfz_kp", f"{_DATA}/gfz/Kp_ap_Ap_SN_F107_since_1932.txt"),
    },
    "forecast_ext": {
        "issued_utc": _at(12, 30),
        "r_scale": "R3",
        "s_scale": "S1",
        "g_scale": "G5",
        **_provenance(
            "three_day_forecast",
            f"{_DATA}/warehouse/forecast/2024/202405111230three_day_forecast.txt",
        ),
    },
}


def make_l1_row(entity: str, **overrides: Any) -> dict[str, Any]:
    """Правильная строка сущности; ``overrides`` меняют отдельные столбцы (проверка не выполняется)."""
    row = dict(_EXAMPLES[entity])
    row.update(overrides)
    return row


assert set(_EXAMPLES) == set(L1_SCHEMAS)
