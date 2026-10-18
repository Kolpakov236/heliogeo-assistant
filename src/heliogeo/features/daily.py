"""Суточные наблюдения: ответ SWX API → значения колонок ``observation_daily``.

Разбор идёт по ответу источника, приведённому к моменту запроса (``SwxDataResponse.data``), а не
по ``UnifiedPayload``: payload уже потерял часть сведений (``earth_directed = null`` превращён в
``False``, нет сектора ММП, нет классов McIntosh и роста областей).

Функция чистая: без сети, БД и системных часов. Возвращает словарь, которым заполняется
``ObservationDailyRecord(**row)``. Значение ``None`` — «нет данных», а не ноль: сутки без вспышек
дают счётчики 0 и ``flare_max_class = None``, а ответ без раздела ``flares`` — ``None`` везде.
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from heliogeo.core.logging import get_logger

logger = get_logger(__name__)

# Классы вспышек, по которым ведётся счёт. A-класс (фон) в счётчик не входит, но на максимальный
# класс влияет.
FLARE_COUNT_CLASSES = ("B", "C", "M", "X")
_FLARE_ORDER = {letter: rank for rank, letter in enumerate("ABCMX")}

# Классы Z по McIntosh (первая буква кода, например «Fkc» → F)
MCINTOSH_Z_CLASSES = frozenset("ABCDEFH")

SECTORS = frozenset({"toward", "away"})

# (!) Трёхчасовых значений Kp в сутках 8. В solar_wind.kp_obs их 24: брать нужно geomag.kp_obs,
# иначе сработает ограничение kp_values_len.
KP_PER_DAY = 8

QC_OK = "ok"
QC_DEGRADED = "degraded"
QC_MISSING = "missing"
QC_INVALID = "invalid"


def as_number(value: Any) -> float | None:
    """Конечное число или ``None``: ``bool``, строки, ``NaN`` и бесконечности — не числа."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _numbers(values: Iterable[Any]) -> list[float]:
    return [number for number in map(as_number, values) if number is not None]


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """Раздел ответа как словарь; ``None``, если его нет или он другого вида."""
    section = data.get(key)
    return section if isinstance(section, Mapping) else None


def _rows(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]] | None:
    """Список событий раздела; ``None``, если раздела нет (это не то же, что пустой список)."""
    rows = data.get(key)
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, Mapping)]


# --- Геомагнитная обстановка ---


def _kp_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    kp_obs = _section(_section(data, "geomag") or {}, "kp_obs") or {}
    values = _numbers(kp_obs.get("values") or [])
    if len(values) > KP_PER_DAY:
        logger.warning(
            "kp_obs.values: %d значений вместо %d, ряд отброшен", len(values), KP_PER_DAY
        )
        return {"kp_max": None, "kp_values": None, "kp_source": None}, QC_INVALID

    kp_max = as_number(kp_obs.get("max"))
    if kp_max is None and values:
        kp_max = max(values)
    source = kp_obs.get("source")
    fields = {
        "kp_max": kp_max,
        "kp_values": values or None,
        "kp_source": source if isinstance(source, str) else None,
    }
    if kp_max is None:
        return fields, QC_MISSING
    # Меньше 8 значений — реальные данные, но неполные сутки
    return fields, QC_OK if len(values) == KP_PER_DAY else QC_DEGRADED


def _dst_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    geomag = _section(data, "geomag") or {}
    solar_wind = _section(data, "solar_wind") or {}
    dst_min = as_number(geomag.get("dst_min_day"))
    if dst_min is None:
        dst_min = as_number((_section(solar_wind, "dst_obs") or {}).get("min"))
    # dst_source (final|provisional|realtime) источник не сообщает: оставляем NULL, а не гадаем
    return {"dst_min": dst_min, "dst_source": None}, QC_OK if dst_min is not None else QC_MISSING


# --- Солнечный ветер ---


def _solar_wind_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    wind = _section(data, "solar_wind")
    if wind is None:
        empty = dict.fromkeys(
            (
                "sw_speed_min",
                "sw_speed_avg",
                "sw_speed_max",
                "sw_bt",
                "sw_bz_min",
                "sw_density_avg",
                "sw_sector",
                "sw_source",
            )
        )
        return empty, QC_MISSING

    speed = _section(wind, "speed") or {}
    imf = _section(wind, "imf") or {}
    density = _section(wind, "density") or {}
    sector = imf.get("sector")
    source = wind.get("source")
    fields = {
        "sw_speed_min": as_number(speed.get("min")),
        "sw_speed_avg": as_number(speed.get("avg")),
        "sw_speed_max": as_number(speed.get("max")),
        "sw_bt": as_number(imf.get("bt")),
        "sw_bz_min": as_number(imf.get("bz_min")),
        # density.latest в архивных ответах пуст; avg — того же смысла, что и средняя скорость
        "sw_density_avg": as_number(density.get("avg")),
        "sw_sector": sector if sector in SECTORS else None,
        "sw_source": source if isinstance(source, str) else None,
    }
    if fields["sw_speed_avg"] is None:
        return fields, QC_MISSING
    # Всё, что не rtsw, — это OMNI (носовая точка ударной волны), а не измерение в L1
    return fields, QC_OK if fields["sw_source"] == "rtsw" else QC_DEGRADED


# --- Вспышки ---


def parse_flare_class(value: Any) -> tuple[str, float] | None:
    """«M3.1» → («M», 3.1); ``None``, если класс не разобрать."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().upper()
    letter = text[0]
    if letter not in _FLARE_ORDER:
        return None
    try:
        magnitude = float(text[1:]) if text[1:] else 1.0
    except ValueError:
        return None
    return (letter, magnitude) if math.isfinite(magnitude) else None


def _flare_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    rows = _rows(data, "flares")
    if rows is None:
        return {"flare_max_class": None, "flare_counts": None}, QC_MISSING

    counts = dict.fromkeys(FLARE_COUNT_CLASSES, 0)
    best: tuple[tuple[int, float], str] | None = None
    unparsed = 0
    for row in rows:
        parsed = parse_flare_class(row.get("class"))
        if parsed is None:
            unparsed += 1
            continue
        letter, magnitude = parsed
        if letter in counts:
            counts[letter] += 1
        rank = (_FLARE_ORDER[letter], magnitude)
        if best is None or rank > best[0]:
            best = (rank, str(row["class"]).strip().upper())
    if unparsed:
        logger.warning("Вспышки без разбираемого класса: %d из %d", unparsed, len(rows))
    return {
        "flare_max_class": best[1] if best else None,
        "flare_counts": counts,
    }, QC_DEGRADED if unparsed else QC_OK


# --- Активные области ---


def _active_region_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    rows = _rows(data, "active_regions")
    if rows is None:
        empty = dict.fromkeys(("ar_count", "ar_total_area", "ar_has_delta", "ar_summary"))
        return empty, QC_MISSING

    total_area = sum(_numbers(row.get("area") for row in rows))
    mcintosh_z: Counter[str] = Counter()
    growth: Counter[str] = Counter()
    has_delta = False
    for row in rows:
        mcintosh = row.get("mcintosh")
        if isinstance(mcintosh, str) and mcintosh[:1].upper() in MCINTOSH_Z_CLASSES:
            mcintosh_z[mcintosh[:1].upper()] += 1
        status = (_section(row, "growth") or {}).get("status")
        if isinstance(status, str) and status:
            growth[status] += 1
        magnetic = row.get("magnetic")
        if isinstance(magnetic, str) and "delta" in magnetic.lower():
            has_delta = True
    return {
        "ar_count": len(rows),
        "ar_total_area": round(total_area),
        "ar_has_delta": has_delta,
        "ar_summary": {"mcintosh_z": dict(mcintosh_z), "growth": dict(growth)},
    }, QC_OK


# --- Корональные выбросы массы ---


def _cme_fields(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    rows = _rows(data, "cmes")
    if rows is None:
        empty = dict.fromkeys(
            (
                "cme_count",
                "cme_earth_directed",
                "cme_direction_unknown",
                "cme_speed_max",
                "cme_width_max",
                "cme_earth_directed_speed_max",
            )
        )
        return empty, QC_MISSING

    # earth_directed: true — к Земле, false — мимо, null — направление не определено. Эти три
    # случая нельзя сводить к двум: «неизвестно» не равно «мимо».
    earth_directed = [row for row in rows if row.get("earth_directed") is True]
    unknown = [row for row in rows if row.get("earth_directed") is None]
    return {
        "cme_count": len(rows),
        "cme_earth_directed": len(earth_directed),
        "cme_direction_unknown": len(unknown),
        "cme_speed_max": max(_numbers(row.get("speed") for row in rows), default=None),
        "cme_width_max": max(_numbers(row.get("width") for row in rows), default=None),
        "cme_earth_directed_speed_max": max(
            _numbers(row.get("speed") for row in earth_directed), default=None
        ),
    }, QC_DEGRADED if unknown else QC_OK


# --- Сборка строки ---


def extract_observation_daily(
    data: Mapping[str, Any], *, d: date, raw_s3_key: str
) -> dict[str, Any]:
    """Ответ ``/data`` источника → значения колонок строки ``observation_daily``.

    Args:
        data: ответ источника, приведённый к моменту запроса (``SwxDataResponse.data``).
        d: сутки UTC, к которым относится строка.
        raw_s3_key: ключ сохранённого сырья. Сырьё сохраняется раньше разбора, поэтому ключ
            обязателен.

    Returns:
        Словарь для ``ObservationDailyRecord(**row)``. Разделы, которых в ответе нет, дают
        ``None`` в своих колонках и статус ``missing`` в ``qc``; сбой разбора одного раздела
        на остальные не влияет.

    Raises:
        ValueError: если ``raw_s3_key`` пуст.
    """
    if not raw_s3_key.strip():
        raise ValueError("raw_s3_key обязателен: сырьё сохраняется раньше разбора")

    row: dict[str, Any] = {"d": d}
    qc: dict[str, str] = {}
    for group, extract in (
        ("kp", _kp_fields),
        ("dst", _dst_fields),
        ("solar_wind", _solar_wind_fields),
        ("flares", _flare_fields),
        ("active_regions", _active_region_fields),
        ("cme", _cme_fields),
    ):
        fields, status = extract(data)
        row.update(fields)
        qc[group] = status
    row["qc"] = qc
    row["raw_s3_key"] = raw_s3_key
    return row
