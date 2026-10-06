"""Маппинг swx JSON → UnifiedPayload.

Все функции — чистые: получают dict, возвращают dict, совместимый с Pydantic.
Никаких HTTP-запросов, никаких side effects.
"""

from typing import Any

from heliogeo.core.logging import get_logger

logger = get_logger(__name__)

PARTICLES_SWPC_END = "2020-03-09"  # последний день SWPC Gp_part
PARTICLES_MPS_HI_START = "2020-03-10"  # NetCDF MPS-HI, нет 5-мин ряда
PARTICLES_MPS_HI_END = "2020-10-31"  # последний день MPS-HI
PARTICLES_SGPS_START = "2020-11-01"  # архив SGPS avg5m, сумма каналов

FORECAST_NCEI_START = "2022-03-01"  # 3-day forecast на NCEI
DISCUSSION_NCEI_START = "2024-03-01"  # Forecast Discussion на NCEI
DAYPRE_START = "2013-01-01"  # daypre как запасной источник
DONKI_START = "2010-01-01"  # DONKI доступен с 2010

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


def map_solar_activity(data: dict[str, Any]) -> dict[str, Any]:
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
    - `temperature` нет в API → None.
    """
    speed = data.get("speed", {})
    density = data.get("density", {})
    imf = data.get("imf", {})

    return {
        "speed_min": speed.get("min"),
        "speed_avg": speed.get("avg"),
        "speed_max": speed.get("max"),
        "density": density.get("latest") or density.get("avg"),
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

    fcst = data.get("fcst") or {}
    kp_forecast = fcst.get("kp_forecast") or []
    kp_fcst = max(kp_forecast) if kp_forecast else None

    return {
        "kp_observed": kp_obs,
        "dst_observed": dst_obs,
        "current_level": _kp_dst_to_level(kp_obs, dst_obs),
        "kp_fcst": kp_fcst,
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


def map_forecast(data: dict[str, Any]) -> dict[str, Any]:
    """geomag.fcst + отсутствие probabilities → Forecast.

    Особенности API:
    - `fcst.g_scale` = "G1" → geomagnetic_g.
    - `flare_c/m/x_prob`, `proton_prob` — нет в API → None.
    - `drivers` — пустой список.
    """
    fcst = data.get("fcst") or {}
    return {
        "flare_c_prob": None,
        "flare_m_prob": None,
        "flare_x_prob": None,
        "proton_prob": None,
        "geomagnetic_g": fcst.get("g_scale"),
        "drivers": [],
    }


# --- Images ---


def map_images(data: dict[str, Any] | None) -> dict[str, Any]:
    """images — не в API, заполняется отдельно из DAV."""
    return {}


# --- Technical Issues ---


def _in_range(d: str, lo: str, hi: str) -> bool:
    """Лексикографическая проверка: работает при строгом YYYY-MM-DD."""
    return lo <= d <= hi


def map_technical_issues(
    notes: list[str],
    solar_wind_source: str,
    snapshot_date: str,
    *,
    has_l1_rtsw: bool = False,
    omni_min_downloaded: bool = False,
    forecast_available: bool = False,
    discussion_available: bool = False,
    donki_lag_days: int | None = None,
    dst_kind: str | None = None,  # "final" | "provisional" | "realtime"
    active_regions_growth: str | None = None,  # "new" | "empty" | None
) -> str | None:
    """Собрать technical_issues из notes и оговорок API.

    Правила:

    Частицы:
      - 2017-01-01 .. 2020-03-09:
        SWPC Gp_part.
      - 2020-03-10 .. 2020-10-31:
        MPS-HI NetCDF; 5-минутного ряда нет.
      - 2020-11-01 .. настоящее:
        SGPS avg5m; proton >10 MeV — сумма каналов SGPS,
        это не операционный SWPC отклик.
      - Суточная флюенция dayind сама по себе эту дыру
        не устраняет.

    Солнечный ветер:
      - RTSW 1-min доступен только как live/recent.
      - Исторический 1-min источник — OMNI HRO.
      - Если HRO не скачан, возможен OMNI2 1-hour.
      - OMNI HRO сдвинут к subsolar bow-shock nose и
        не является сырым L1 DSCOVR/ACE.
      - Для реального L1 ACE/Wind это предупреждение
        про OMNI HRO не добавляется.

    Прогнозы:
      - NCEI 3-day forecast существует с 2022-03-01.
      - До этого используется daypre (с 2013-01-01)
        и наблюдаемый Kp.
      - Forecast Discussion NCEI существует с 2024-03-01.
      - Для более ранних дат query discussion возвращает RSGA.
      - Если для сегодняшней/актуальной даты архивного выпуска
        ещё нет, используется live discussion.txt.

    DONKI:
      - доступен с 2010-01-01;
      - кинематика может отставать;
      - earth_directed — эвристика, а не список попадания
        WSA-ENLIL.

    Dst:
      - final предпочтительнее provisional;
      - provisional предпочтительнее realtime;
      - realtime требует явной оговорки.

    Active regions:
      - growth считается относительно предыдущего файла;
      - если предыдущего файла нет, статус может быть "new"
        или "empty"; в обоих случаях добавляется оговорка.
    """

    issues: list[str] = list(notes or [])

    if snapshot_date >= PARTICLES_SGPS_START:
        issues.append("Proton >10 MeV: SGPS channels sum, not SWPC operational")

    elif _in_range(
        snapshot_date,
        PARTICLES_MPS_HI_START,
        PARTICLES_MPS_HI_END,
    ):
        issues.append(
            "Proton >10 MeV: no 5-min series "
            "(NetCDF MPS-HI; archive SGPS avg5m starts 2020-11)"
        )

    if solar_wind_source != "rtsw":
        issues.append(f"Solar wind source: {solar_wind_source}")

    if not has_l1_rtsw:
        issues.append(
            "1-min RTSW available only as live (recent hours/day); "
            "history via OMNI HRO 1-min or OMNI2 1-hour"
        )

    if solar_wind_source == "omni_hro":
        issues.append(
            "OMNI HRO shifted to subsolar bow shock nose " "(not raw DSCOVR/ACE)"
        )

    elif solar_wind_source == "omni2":
        issues.append(
            "Solar wind history: OMNI2 1-hour used; " "OMNI HRO 1-min not downloaded"
        )

    if snapshot_date < FORECAST_NCEI_START:
        issues.append(
            f"3-day forecast: NCEI archive starts {FORECAST_NCEI_START}; "
            f"before that only daypre (from {DAYPRE_START}) and observed Kp"
        )
    elif not forecast_available:
        issues.append(
            "3-day forecast: expected NCEI archive product is unavailable "
            "for this date"
        )
    if not discussion_available:
        if snapshot_date < DISCUSSION_NCEI_START:
            issues.append(
                f"Forecast Discussion: NCEI archive starts "
                f"{DISCUSSION_NCEI_START}; earlier query returns RSGA"
            )
        else:
            issues.append(
                "Forecast Discussion: live discussion.txt used "
                "(no archived issue for today yet)"
            )

    if (
        snapshot_date >= DONKI_START
        and donki_lag_days is not None
        and donki_lag_days > 0
    ):
        issues.append(
            f"DONKI kinematics may lag by {donki_lag_days} day(s); "
            "earth_directed is a heuristic, not WSA-ENLIL hit list"
        )

    if dst_kind == "realtime":
        issues.append("Kyoto Dst: only realtime available (recent months)")

    elif dst_kind == "provisional":
        issues.append("Kyoto Dst: provisional (final not yet published)")

    if active_regions_growth == "new":
        issues.append("Active region growth: no previous file; growth marked 'new'")
    elif active_regions_growth in (None, "empty"):
        issues.append("Active region growth: no previous file, growth empty")

    return " | ".join(issues) if issues else None
