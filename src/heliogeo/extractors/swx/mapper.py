"""Маппинг swx JSON → UnifiedPayload.

Все функции — чистые: получают dict, возвращают dict, совместимый с Pydantic.
Никаких HTTP-запросов, никаких side effects.
"""

from collections.abc import Mapping
from typing import Any

from heliogeo.core.logging import get_logger

logger = get_logger(__name__)


# --- Solar Activity ---


def map_active_regions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """active_regions[] → list[ActiveRegion].

    Особенности API:
    - `growth` — объект, а не строка. Берём `growth.status`.
    - Есть `carrington_lon`, `extent`, `observed_date` — не в схеме, игнорируем.
    """
    result: list[dict[str, Any]] = []
    for row in rows:
        growth_obj = row.get("growth") or {}
        result.append(
            {
                "noaa_num": row.get("noaa"),
                "location": row.get("location", ""),
                "mcintosh": row.get("mcintosh"),
                "magnetic_class": row.get("magnetic"),
                "area": row.get("area"),
                "spots": row.get("spots"),
                "growth": growth_obj.get("status"),
            }
        )
    return result


def map_flares(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """flares[] → list[Flare].

    Особенности API:
    - `time` берём из `max` (пик), fallback — `begin`.
    - `class` → `class_` (alias в Pydantic).
    - `radio` → `radio_signatures`.
    """
    result: list[dict[str, Any]] = []
    for row in rows:
        time = row.get("max") or row.get("begin")
        if not time:
            logger.warning("Flare without time: %s", row)
            continue
        result.append(
            {
                "time": time,
                "class": row.get("class", "C1"),
                "ar": row.get("ar"),
                "radio_signatures": row.get("radio", []) or [],
            }
        )
    return result


def map_cmes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """cmes[] → list[CME].

    Особенности API:
    - `earth_directed` может быть null → False.
    - `speed` может отсутствовать → 0.0.
    """
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "time": row.get("time"),
                "speed": float(row.get("speed") or 0.0),
                "width": float(row.get("width") or 0.0),
                "earth_directed": bool(row.get("earth_directed") or False),
            }
        )
    return result


def map_solar_activity(data: Mapping[str, Any]) -> dict[str, Any]:
    """Секция solar_activity целиком."""
    return {
        "active_regions": map_active_regions(data.get("active_regions", [])),
        "flares_last_24h": map_flares(data.get("flares", [])),
        "cme_list": map_cmes(data.get("cmes", [])),
        "f107": None,  # нет в API
        "ssn": None,  # нет в API
    }


# --- Energetic Particles ---


def map_particles(data: dict[str, Any]) -> dict[str, Any]:
    """particles → EnergeticParticles.

    Особенности API:
    - `latest`, `peak`, `background` могут быть null (нет 5-мин ряда).
    - `daily_fluence` — есть, но это не то же самое, что `latest`.
    - `sep.gt10_event` → `sep_flag`.
    """
    e = data.get("electron_gt2mev", {})
    p10 = data.get("proton_gt10mev", {})
    p100 = data.get("proton_gt100mev", {})
    sep = data.get("sep", {})

    return {
        "e_2mev": e.get("latest"),
        "p_10mev_peak": p10.get("peak"),
        "p_10mev_background": p10.get("background"),
        "p_100mev_peak": p100.get("peak"),
        "sep_flag": bool(sep.get("gt10_event", False)),
    }


# --- Solar Wind ---


def map_solar_wind(data: dict[str, Any]) -> dict[str, Any]:
    """solar_wind → SolarWind.

    Особенности API:
    - `source` = rtsw | omni_hro_1min | omni2_hourly. Маппим в unknown.
    - `phi_deg` → `phi`.
    - `density` — среднее за окно (`avg`), как и скорость. `latest` не берём: в архивных ответах
      он `null`, и значение менялось бы по смыслу от дня к дню (то последнее, то среднее).
    - `temperature` нет в API → None.
    """
    speed = data.get("speed", {})
    density = data.get("density", {})
    imf = data.get("imf", {})

    return {
        "speed_min": speed.get("min"),
        "speed_avg": speed.get("avg"),
        "speed_max": speed.get("max"),
        "density": density.get("avg"),
        "bt": imf.get("bt"),
        "bz_min": imf.get("bz_min"),
        "bz_max": imf.get("bz_max"),
        "phi": imf.get("phi_deg"),
        "temperature": None,  # нет в API
        "source": "unknown",  # CH HSS / CME определить нельзя без эвристики
    }


# --- Geospace ---


def map_geospace(data: dict[str, Any]) -> dict[str, Any]:
    """geomag → Geospace.

    Особенности API:
    - `kp_obs` — объект {values, max, source}. Берём `max`.
    - `dst_obs` — число.
    - `current_level` — вычислим по Kp/Dst.
    - `kp_fcst` — из `fcst.kp_forecast` (может быть пусто).
    """
    kp_obj = data.get("kp_obs") or {}
    kp_obs = kp_obj.get("max")
    dst_obs = data.get("dst_obs")

    return {
        "kp_observed": kp_obs,
        "dst_observed": dst_obs,
        "current_level": _kp_dst_to_level(kp_obs, dst_obs),
        "kp_fcst": _max_kp_forecast(data.get("fcst")),
    }


def _kp_dst_to_level(kp: float | None, dst: float | None) -> str:
    """Kp/Dst → quiet | active | storm."""
    if kp is not None and kp >= 5:
        return "storm"
    if dst is not None and dst <= -50:
        return "storm"
    if kp is not None and kp >= 4:
        return "active"
    if dst is not None and dst <= -30:
        return "active"
    return "quiet"


# --- Forecast ---

# Нижняя граница Kp для шкал NOAA G5…G1. Kp приходит третями (4.67 = 5-, 5.00 = 5, 5.33 = 5+), а
# G1 — это «Kp = 5», поэтому порог — целое значение и выше (5.67 всё ещё G1).
_G_SCALE_BY_KP = ((9.0, "G5"), (8.0, "G4"), (7.0, "G3"), (6.0, "G2"), (5.0, "G1"))


def _max_kp_forecast(fcst: Mapping[str, Any] | None) -> float | None:
    """Максимум числовой сетки Kp трёхдневного прогноза; ``None``, если сетки нет.

    Источник отдаёт ``fcst.kp_forecast`` списком трёхчасовых значений (8 интервалов × 3 суток).
    Значения, которые не числа (``null``, строки, bool), пропускаются.
    """
    values = [
        value
        for value in (fcst or {}).get("kp_forecast") or []
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    return max(values) if values else None


def _g_scale_from_kp(kp: float | None) -> str | None:
    """Максимум Kp → шкала NOAA G1–G5; ``None``, если Kp неизвестен или ниже 5 (бури нет)."""
    if kp is None:
        return None
    for threshold, scale in _G_SCALE_BY_KP:
        if kp >= threshold:
            return scale
    return None


def map_forecast(data: dict[str, Any]) -> dict[str, Any]:
    """geomag.fcst + отсутствие probabilities → Forecast.

    Особенности API:
    - `geomagnetic_g` — шкала G по максимуму числовой сетки Kp (`fcst.kp_forecast`): G1 при Kp ≥ 5,
      G2 при ≥ 6, …, G5 при 9. `None`, если сетки нет (сейчас источник её не отдаёт) или Kp не
      дотягивает до G1.
    - `fcst.g_scale` не используется: источник берёт его из первого вхождения «G1…G5» в тексте
      прогноза, поэтому в спокойные дни там «G1» из фразы «No G1 … are expected».
    - `flare_c/m/x_prob`, `proton_prob` — нет в API → None.
    - `drivers` — пустой список.
    """
    return {
        "flare_c_prob": None,
        "flare_m_prob": None,
        "flare_x_prob": None,
        "proton_prob": None,
        "geomagnetic_g": _g_scale_from_kp(_max_kp_forecast(data.get("fcst"))),
        "drivers": [],
    }


# --- Images ---


def map_images(data: dict[str, Any] | None) -> dict[str, Any]:
    """images — не в API, заполняется отдельно из DAV."""
    return {}


# --- Technical Issues ---


def map_technical_issues(
    notes: list[str],
    solar_wind_source: str,
    snapshot_date: str,
) -> str | None:
    """Собрать technical_issues из notes и оговорок API.

    Оговорки:
    - source != rtsw → данные OMNI, не L1.
    - snapshot_date >= 2020-11-01 → SGPS >10 MeV не операционный.
    """
    issues: list[str] = list(notes)

    if solar_wind_source != "rtsw":
        issues.append(f"Solar wind source: {solar_wind_source} " f"(OMNI bow shock nose, not L1)")

    if snapshot_date >= "2020-11-01":
        issues.append("Proton >10 MeV: SGPS channels sum, not SWPC operational")

    return " | ".join(issues) if issues else None
