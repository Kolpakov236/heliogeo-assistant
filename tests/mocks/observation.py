"""Моки строк observation_daily: значения взяты из реальных ответов SWX API (fixtures/).

Спокойные сутки 2026-09-01 и буря 2024-05-11 — те же снимки, на которых строятся тесты
экстрактора. Любое поле переопределяется: ``make_observation_daily(kp_max=5.0)``.
"""

from datetime import date
from typing import Any

from heliogeo.storage.models import ObservationDailyRecord


def make_observation_daily(**overrides: Any) -> ObservationDailyRecord:
    """Спокойные сутки 2026-09-01 (fixtures/swx_snapshot_2026-09-01.json)."""
    values: dict[str, Any] = {
        "d": date(2026, 9, 1),
        "kp_max": 2.333,
        "kp_values": [0.667, 1.333, 1.667, 2.333, 1.667, 1.333, 1.667, 1.333],
        "kp_source": "gfz",
        "dst_min": -15.0,
        "dst_source": "provisional",
        "sw_speed_min": 347.9,
        "sw_speed_avg": 398.93,
        "sw_speed_max": 449.6,
        "sw_bt": 5.17,
        "sw_bz_min": -5.71,
        "sw_density_avg": 4.084,
        "sw_sector": "away",
        "sw_source": "omni_hro_1min",
        "flare_max_class": "C5.7",
        "flare_counts": {"B": 5, "C": 3, "M": 0, "X": 0},
        "ar_count": 6,
        "ar_total_area": 180,
        "ar_has_delta": False,
        "ar_summary": {
            "mcintosh_z": {"B": 1, "C": 2, "D": 1, "H": 2},
            "growth": {"continuing": 3, "new": 3},
        },
        "cme_count": 3,
        "cme_earth_directed": 0,
        "cme_direction_unknown": 1,  # у одного выброса earth_directed = null
        "cme_speed_max": 440.0,
        "cme_width_max": 54.0,
        "cme_earth_directed_speed_max": None,  # выбросов к Земле не было
        "qc": {"kp": "ok", "cme": "degraded"},
        "raw_s3_key": "swx/data/2026/09/01/1200_24h_nolive_0123456789ab.json",
    }
    values.update(overrides)
    return ObservationDailyRecord(**values)


def make_storm_observation_daily(**overrides: Any) -> ObservationDailyRecord:
    """Буря 2024-05-11 (fixtures/swx_snapshot_2024-05-11.json): Kp до 9, Dst до -406 нТл."""
    values: dict[str, Any] = {
        "d": date(2024, 5, 11),
        "kp_max": 9.0,
        "kp_values": [9.0, 8.333, 8.333, 9.0, 8.667, 8.333, 7.667, 7.667],
        "dst_min": -406.0,
        "sw_speed_min": 655.3,
        "sw_speed_avg": 781.61,
        "sw_speed_max": 955.0,
        "sw_bt": 7.7,
        "sw_bz_min": -47.85,
        "sw_density_avg": 15.647,  # «latest» в этот день пуст, есть только «avg»
        "sw_sector": "toward",
        "flare_max_class": "X5.8",
        "flare_counts": {"B": 0, "C": 4, "M": 5, "X": 2},
        "ar_count": 7,
        "ar_total_area": 3110,
        "ar_has_delta": True,
        "ar_summary": {"mcintosh_z": {"C": 1, "F": 2, "H": 4}, "growth": {"continuing": 7}},
        "cme_count": 5,
        "cme_earth_directed": 2,
        "cme_direction_unknown": 1,
        "cme_speed_max": 1263.0,
        "cme_width_max": 102.0,
        "cme_earth_directed_speed_max": 1263.0,
        "raw_s3_key": "swx/data/2024/05/11/1200_24h_nolive_ba9876543210.json",
    }
    values.update(overrides)
    return ObservationDailyRecord(**values)


def make_empty_observation_daily(
    d: date = date(2026, 9, 2), **overrides: Any
) -> ObservationDailyRecord:
    """Сутки без данных: заполнены только дата и ключ сырья, остальное NULL («нет данных»)."""
    values: dict[str, Any] = {
        "d": d,
        "raw_s3_key": f"swx/data/{d:%Y/%m/%d}/0000_24h_nolive_000000000000.json",
    }
    values.update(overrides)
    return ObservationDailyRecord(**values)
