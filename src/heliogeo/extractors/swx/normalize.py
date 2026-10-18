"""Нормализация ответа ``/data`` в строки слоя L1.

Чистая функция: на входе типизированный ответ и ключ сохранённого тела в ``raw``, на выходе строки
семи сущностей по схемам ``heliogeo.schema.l1``. Ни сети, ни диска, ни часов.

Принципы:

* **Неизвестное остаётся ``None``.** Пустая строка источника, ``null`` и ноль там, где физический
  ноль невозможен, превращаются в ``None`` (ноль — с записью в ``problems``). Ничего не
  подставляется по умолчанию: ни класс вспышки, ни ``earth_directed``, ни скорость выброса.
* **Строка либо правильная, либо не записывается.** Строка, не прошедшая схему, отбрасывается и
  попадает в ``problems``; в L1 не попадает ничего, что нарушает контракт.
* **Раздел L1 — полные сутки.** Ответ должен покрывать целое окно ``ut_day`` и быть получен не
  раньше его конца: иначе события «из будущего» отсечены, а агрегаты неполны, и в озеро легли бы
  сутки, которых на самом деле нет. Нужное время запроса — последняя минута суток.
* **Прогнозные шкалы источника не переносятся.** ``r_scale`` и ``s_scale`` из ответа — константа
  из-за ошибки разбора, ``g_scale`` — отрицание, принятое за утверждение (см.
  ``docs/data_sources.md``). Шкала G считается по числовой сетке Kp, как и в маппере; пока сетки
  нет, она ``None``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.availability import parse_event_time, parse_issued
from heliogeo.extractors.swx.mapper import _g_scale_from_kp, _max_kp_forecast
from heliogeo.extractors.swx.response import SwxDataResponse
from heliogeo.schema.l1 import L1_SCHEMAS, validate_row

logger = get_logger(__name__)

# Продукт источника, из которого взята строка. Для ветра он назван в самом ответе.
_PRODUCT = {
    "active_regions": "srs",
    "flares": "events",
    "cmes": "donki_cme",
    "particles": "dayind",
    "geomag_obs": "gfz_kp",
    "forecast_ext": "three_day_forecast",
}
# Название ряда ветра в ответе -> продукт в ``sources[]``, если они различаются.
_WIND_PRODUCT = {"omni2_hourly": "omni2"}
_DAY_WINDOWS = (None, "ut_day")
_PROVENANCE = frozenset({"source_product", "source_path", "raw_key"})


class NormalizeError(ValueError):
    """Ответ нельзя превратить в раздел L1 целиком (не сутки, неполные сутки)."""


@dataclass(frozen=True, kw_only=True)
class L1Partition:
    """Раздел L1: одни сутки, строки всех семи сущностей.

    Attributes:
        day: дата раздела (UTC), значение ``dt`` в ключе.
        rows: строки по сущностям; ключи — все семь сущностей, у отсутствующих — пустой кортеж.
        problems: что в ответе не удалось перенести: отброшенные строки, подмены нуля, дубли.
    """

    day: date
    rows: Mapping[str, tuple[dict[str, Any], ...]]
    problems: tuple[str, ...] = ()


class _Context:
    """Общее для разбора: происхождение строк и список замечаний."""

    def __init__(self, response: SwxDataResponse, raw_key: str) -> None:
        self.raw_key = raw_key
        self.paths = {source.product: source.path for source in response.sources}
        self.problems: list[str] = []

    def provenance(self, product: str) -> dict[str, Any]:
        return {
            "source_product": product,
            "source_path": self.paths.get(product),
            "raw_key": self.raw_key,
        }

    def note(self, label: str, message: str) -> None:
        self.problems.append(f"{label}: {message}")


# --- Приведение значений: пустое — None, чужой тип — замечание и None ---


def _number(value: object, label: str, ctx: _Context) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        ctx.note(label, f"ожидалось число, получено {value!r}")
        return None
    return float(value) if isinstance(value, float) else value


def _whole(value: object, label: str, ctx: _Context) -> int | None:
    number = _number(value, label, ctx)
    if number is None:
        return None
    if isinstance(number, float) and not number.is_integer():
        ctx.note(label, f"ожидалось целое, получено {value!r}")
        return None
    return int(number)


def _text(value: object, label: str, ctx: _Context) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        ctx.note(label, f"ожидалась строка, получено {value!r}")
        return None
    return value if value.strip() else None


def _flag(value: object, label: str, ctx: _Context) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        ctx.note(label, f"ожидалось да/нет, получено {value!r}")
        return None
    return value


def _texts(value: object, label: str, ctx: _Context) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        ctx.note(label, f"ожидался список, получено {value!r}")
        return None
    return [item for item in value if isinstance(item, str) and item.strip()]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _issued(value: object) -> datetime | None:
    """Время выпуска отчёта: у областей оно записано без «UTC» (``2024 May 11 0030``)."""
    if not isinstance(value, str):
        return None
    return parse_issued(value) or parse_issued(f"{value.strip()} UTC")


def _day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# --- Сущности ---


def _active_regions(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    rows = []
    for index, src in enumerate(_rows(data.get("active_regions"))):
        label = f"active_regions[{index}]"
        growth = _mapping(src.get("growth"))
        rows.append(
            {
                "noaa_number": _whole(src.get("noaa"), f"{label}.noaa", ctx),
                "observed_date": _day(src.get("observed_date")),
                "issued_utc": _issued(src.get("issued")),
                "location": _text(src.get("location"), f"{label}.location", ctx),
                "carrington_lon_deg": _whole(
                    src.get("carrington_lon"), f"{label}.carrington_lon", ctx
                ),
                "area_msh": _whole(src.get("area"), f"{label}.area", ctx),
                "extent_deg": _whole(src.get("extent"), f"{label}.extent", ctx),
                "spots": _whole(src.get("spots"), f"{label}.spots", ctx),
                "mcintosh": _text(src.get("mcintosh"), f"{label}.mcintosh", ctx),
                "magnetic": _text(src.get("magnetic"), f"{label}.magnetic", ctx),
                "growth_status": _text(growth.get("status"), f"{label}.growth.status", ctx),
                "growth_area_delta_msh": _whole(
                    growth.get("area_delta"), f"{label}.growth.area_delta", ctx
                ),
                "growth_spots_delta": _whole(
                    growth.get("spots_delta"), f"{label}.growth.spots_delta", ctx
                ),
                **ctx.provenance(_PRODUCT["active_regions"]),
            }
        )
    return rows


def _flares(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    rows = []
    for index, src in enumerate(_rows(data.get("flares"))):
        label = f"flares[{index}]"
        rows.append(
            {
                "begin_utc": parse_event_time(src.get("begin")),
                "max_utc": parse_event_time(src.get("max")),
                "end_utc": parse_event_time(src.get("end")),
                "flare_class": _text(src.get("class"), f"{label}.class", ctx),
                "active_region": _whole(src.get("ar"), f"{label}.ar", ctx),
                "location": _text(src.get("location"), f"{label}.location", ctx),
                "observatory": _text(src.get("observatory"), f"{label}.observatory", ctx),
                "radio": _texts(src.get("radio"), f"{label}.radio", ctx),
                **ctx.provenance(_PRODUCT["flares"]),
            }
        )
    return rows


def _cmes(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    rows = []
    for index, src in enumerate(_rows(data.get("cmes"))):
        label = f"cmes[{index}]"
        rows.append(
            {
                "cme_id": _text(src.get("id"), f"{label}.id", ctx),
                "time_utc": parse_event_time(src.get("time")),
                "speed_km_s": _number(src.get("speed"), f"{label}.speed", ctx),
                "width_deg": _number(src.get("width"), f"{label}.width", ctx),
                "half_angle_deg": _number(src.get("half_angle"), f"{label}.half_angle", ctx),
                "lat_deg": _number(src.get("lat"), f"{label}.lat", ctx),
                "lon_deg": _number(src.get("lon"), f"{label}.lon", ctx),
                "cme_type": _text(src.get("type"), f"{label}.type", ctx),
                "earth_directed": _flag(src.get("earth_directed"), f"{label}.earth_directed", ctx),
                "catalog": _text(src.get("catalog"), f"{label}.catalog", ctx),
                "source_location": _text(
                    src.get("source_location"), f"{label}.source_location", ctx
                ),
                "active_region": _whole(src.get("active_region"), f"{label}.active_region", ctx),
                "note": _text(src.get("note"), f"{label}.note", ctx),
                **ctx.provenance(_PRODUCT["cmes"]),
            }
        )
    return rows


def _particles(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    section = _mapping(data.get("particles"))
    if not section:
        return []
    row: dict[str, Any] = {}
    for channel in ("electron_gt2mev", "proton_gt10mev", "proton_gt100mev"):
        src = _mapping(section.get(channel))
        label = f"particles.{channel}"
        row[f"{channel}_fluence_cm2_sr_day"] = _number(
            src.get("daily_fluence"), f"{label}.daily_fluence", ctx
        )
        row[f"{channel}_latest_pfu"] = _number(src.get("latest"), f"{label}.latest", ctx)
        row[f"{channel}_peak_pfu"] = _number(src.get("peak"), f"{label}.peak", ctx)
        row[f"{channel}_background_pfu"] = _number(
            src.get("background"), f"{label}.background", ctx
        )
        if channel == "electron_gt2mev":
            row[f"{channel}_alert"] = _text(src.get("alert"), f"{label}.alert", ctx)
    sep = _mapping(section.get("sep"))
    row.update(
        {
            "sep_s_scale": _text(sep.get("s_scale"), "particles.sep.s_scale", ctx),
            "sep_gt10_event": _flag(sep.get("gt10_event"), "particles.sep.gt10_event", ctx),
            "sep_gt100_event": _flag(sep.get("gt100_event"), "particles.sep.gt100_event", ctx),
            "sep_alert_flags": _texts(sep.get("alert_flags"), "particles.sep.alert_flags", ctx),
            "sep_note": _text(sep.get("note"), "particles.sep.note", ctx),
        }
    )
    return [{**row, **ctx.provenance(_PRODUCT["particles"])}]


def _solar_wind(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    section = _mapping(data.get("solar_wind"))
    if not section:
        return []
    speed, density, imf = (_mapping(section.get(k)) for k in ("speed", "density", "imf"))
    name = _text(section.get("source"), "solar_wind.source", ctx)
    product = _WIND_PRODUCT.get(name, name) if name else None
    if product is None:
        ctx.note("solar_wind", "в ответе не назван ряд ветра: строка не построена")
        return []
    row = {
        "n_minutes": _whole(section.get("n_minutes"), "solar_wind.n_minutes", ctx),
        "speed_min_km_s": _number(speed.get("min"), "solar_wind.speed.min", ctx),
        "speed_avg_km_s": _number(speed.get("avg"), "solar_wind.speed.avg", ctx),
        "speed_max_km_s": _number(speed.get("max"), "solar_wind.speed.max", ctx),
        "density_latest_cm3": _number(density.get("latest"), "solar_wind.density.latest", ctx),
        "density_avg_cm3": _number(density.get("avg"), "solar_wind.density.avg", ctx),
        "bt_nt": _number(imf.get("bt"), "solar_wind.imf.bt", ctx),
        "bt_avg_nt": _number(imf.get("bt_avg"), "solar_wind.imf.bt_avg", ctx),
        "bz_min_nt": _number(imf.get("bz_min"), "solar_wind.imf.bz_min", ctx),
        "bz_max_nt": _number(imf.get("bz_max"), "solar_wind.imf.bz_max", ctx),
        "bz_latest_nt": _number(imf.get("bz_latest"), "solar_wind.imf.bz_latest", ctx),
        "phi_deg": _number(imf.get("phi_deg"), "solar_wind.imf.phi_deg", ctx),
        "sector": _text(imf.get("sector"), "solar_wind.imf.sector", ctx),
        "wind_source": name,
        **ctx.provenance(product),
    }
    return [row]


def _geomag_obs(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    section = _mapping(data.get("geomag"))
    if not section:
        return []
    kp = _mapping(section.get("kp_obs"))
    values = kp.get("values")
    kp_3h = None
    if isinstance(values, Sequence) and not isinstance(values, str | bytes):
        kp_3h = [_number(v, f"geomag.kp_obs.values[{i}]", ctx) for i, v in enumerate(values)]
    elif values is not None:
        ctx.note("geomag.kp_obs.values", f"ожидался список, получено {values!r}")
    return [
        {
            "kp_3h": kp_3h,
            "kp_max": _number(kp.get("max"), "geomag.kp_obs.max", ctx),
            "kp_est_now": _number(section.get("kp_est_now"), "geomag.kp_est_now", ctx),
            "kp_source": _text(kp.get("source"), "geomag.kp_obs.source", ctx),
            "dst_latest_nt": _number(section.get("dst_obs"), "geomag.dst_obs", ctx),
            "dst_min_nt": _number(section.get("dst_min_day"), "geomag.dst_min_day", ctx),
            **ctx.provenance(_PRODUCT["geomag_obs"]),
        }
    ]


def _forecast_ext(data: Mapping[str, Any], ctx: _Context) -> list[dict[str, Any]]:
    fcst = _mapping(_mapping(data.get("geomag")).get("fcst"))
    issued = parse_issued(fcst.get("raw_issued"))
    if issued is None:
        return []
    return [
        {
            "issued_utc": issued,
            "r_scale": None,
            "s_scale": None,
            "g_scale": _g_scale_from_kp(_max_kp_forecast(fcst)),
            **ctx.provenance(_PRODUCT["forecast_ext"]),
        }
    ]


_BUILDERS = {
    "active_regions": _active_regions,
    "flares": _flares,
    "cmes": _cmes,
    "particles": _particles,
    "solar_wind": _solar_wind,
    "geomag_obs": _geomag_obs,
    "forecast_ext": _forecast_ext,
}


# --- Проверка и сборка раздела ---


def _zero_to_none(entity: str, row: dict[str, Any], label: str, ctx: _Context) -> None:
    """Ноль в столбце, где он физически невозможен, — подмена неизвестного: заменить и сказать."""
    for column in L1_SCHEMAS[entity].columns:
        if column.zero_means_unknown and row.get(column.name) == 0:
            row[column.name] = None
            ctx.note(f"{label}.{column.name}", "ноль в источнике заменён на None")


def _accept(entity: str, rows: list[dict[str, Any]], ctx: _Context) -> tuple[dict[str, Any], ...]:
    spec = L1_SCHEMAS[entity]
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(rows):
        label = f"{entity}[{index}]"
        _zero_to_none(entity, row, label, ctx)
        problems = validate_row(entity, row)
        if problems:
            ctx.problems.extend(f"{label}: строка отброшена: {problem}" for problem in problems)
            continue
        if not spec.key and all(
            row[c.name] is None for c in spec.columns if c.name not in _PROVENANCE
        ):
            ctx.note(label, "строка не построена: все значения неизвестны")
            continue
        key = tuple(row[name] for name in spec.key)
        if spec.key and key in seen:
            ctx.note(label, f"строка отброшена: ключ {key} уже есть")
            continue
        seen.add(key)
        accepted.append(row)
    return tuple(accepted)


def _check_full_day(response: SwxDataResponse) -> date:
    if response.window_spec not in _DAY_WINDOWS:
        raise NormalizeError(f"L1 строится из суточного окна, запрошено {response.window_spec!r}")
    start, end = response.window_start, response.window_end
    if start is None or end is None:
        raise NormalizeError("в ответе нет разобранного окна: границы суток неизвестны")
    if start.date() != end.date() or (start.hour, start.minute) != (0, 0):
        raise NormalizeError(f"окно ответа {start.isoformat()} … {end.isoformat()} не сутки UTC")
    if response.requested_at < end.replace(second=0, microsecond=0):
        raise NormalizeError(
            f"ответ получен на {response.requested_at.isoformat()}, раньше конца суток "
            f"{end.isoformat()}: события отсечены, агрегаты неполны"
        )
    return start.date()


def normalize_response(response: SwxDataResponse, *, raw_key: str) -> L1Partition:
    """Раздел L1 из ответа ``/data``.

    Args:
        response: типизированный ответ; берётся его ``data`` (приведённое к моменту запроса тело).
        raw_key: ключ сохранённого тела ответа в бакете ``raw`` (``RawStore.save_snapshot``);
            попадает в каждую строку как происхождение.

    Raises:
        NormalizeError: если окно ответа — не целые сутки UTC или ответ получен раньше конца
            суток.
    """
    day = _check_full_day(response)
    ctx = _Context(response, raw_key)
    if not response.no_live:
        ctx.note("response", "no_live=False: ответ мог содержать live-данные и не воспроизводим")
    rows = {
        entity: _accept(entity, build(response.data, ctx), ctx)
        for entity, build in _BUILDERS.items()
    }
    for entity, accepted in rows.items():
        logger.debug("L1 %s %s: %d rows", day.isoformat(), entity, len(accepted))
    return L1Partition(day=day, rows=rows, problems=tuple(ctx.problems))
