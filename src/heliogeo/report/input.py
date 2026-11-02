"""Вход отчёта «за сутки»: наблюдаемые значения, сигналы и прогнозы по четырём разделам Discussion.

Из сохранённого ответа ``/data`` за сутки D, признаков за эти сутки, сработавших сигналов и прогнозов
Kp собирается один словарь (``ReportInput.to_payload``). Он идёт в промпт как «зафиксированные
наблюдения» и служит основой проверки чисел (``check_grounding`` ищет числа текста среди чисел
словаря). Всё, о чём модель вправе писать, лежит здесь; чего здесь нет — упоминать нельзя.

* **Раздел** — Solar Activity, Energetic Particle, Solar Wind или Geospace. Сигнал относится к
  разделу по группе признаков из его условия (для «данных нет» — из ``on_missing``); итог («всё в
  норме», «спокойно по неполным данным») общий.
* **Время событий** дано готовой строкой ``DD/HHMM UTC`` (так пишет NOAA): ISO-время «07:36:00» с
  «01/0736» не совпало бы при проверке чисел.
* **Направление выбросов** по отдельным выбросам не передаётся: в payload «не определено» стало
  «нет». Верные числа — признаки ``cme_earth_directed`` и ``cme_direction_unknown``.
* Выпуск датируется 00:30 следующих суток (Discussion 00:30 описывает предыдущие 24 часа).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from heliogeo.contracts.features import FeatureGroup, FeaturesContract, load_feature_specs
from heliogeo.contracts.signals import Condition, SignalRule, SignalsContract, load_signals
from heliogeo.extractors.swx.extractor import SwxExtractorError, payload_from_response
from heliogeo.features.compute import FeatureRow
from heliogeo.features.daily import parse_flare_class
from heliogeo.features.day import compute_day_features, end_of_day, lookback_days
from heliogeo.forecast.kp import KpForecast, KpForecaster, default_kp_forecasters, run_forecasters
from heliogeo.functions.history import data_key, history_from_raw
from heliogeo.functions.runtime import FunctionError
from heliogeo.functions.stored import load_data_response
from heliogeo.schema import UnifiedPayload
from heliogeo.signals import SignalSelection, select_signals
from heliogeo.storage.objects import ObjectStore

SECTIONS = ("Solar Activity", "Energetic Particle", "Solar Wind", "Geospace")
ISSUE_OFFSET = timedelta(minutes=30)
VALIDITY = timedelta(hours=24)
TOP_FLARES, TOP_REGIONS, TOP_CMES = 3, 5, 3

_SECTION_BY_GROUP = {
    FeatureGroup.SOLAR_ACTIVITY: "Solar Activity",
    FeatureGroup.CME: "Solar Activity",
    FeatureGroup.SOLAR_WIND: "Solar Wind",
    FeatureGroup.ACTIVITY_HISTORY: "Geospace",
    FeatureGroup.RECURRENCE: "Geospace",
    FeatureGroup.EXTERNAL_FORECAST: "Geospace",
}
_KEYS = {
    "Solar Activity": (
        "flares_c_24h", "flares_m_24h", "flares_x_24h", "ar_count",
        "ar_new_count", "ar_total_area", "ar_has_delta", "ar_max_mcintosh_z", "cme_count_24h",
        "cme_earth_directed", "cme_direction_unknown", "cme_speed_max_24h", "cme_width_max_24h",
        "cme_earth_directed_speed_max_24h", "days_since_last_m", "days_since_last_x",
    ),
    "Solar Wind": (
        "sw_speed_min", "sw_speed_avg", "sw_speed_max", "sw_density_avg", "sw_bt", "sw_bz_min",
        "sw_sector", "sw_speed_trend_24h",
    ),
    "Geospace": (
        "kp_max_1d", "kp_mean_1d", "kp_max_3d", "kp_trend_3d", "dst_min_1d", "kp_recurrence_27d",
        "swpc_g_scale",
    ),
}  # fmt: skip


class ReportInputError(Exception):
    """Вход отчёта не собрать: за сутки нет данных или они не разбираются."""


@dataclass(frozen=True, kw_only=True)
class SectionInput:
    """Один раздел: что наблюдалось, какие сигналы сработали и что учесть при написании."""

    name: str
    observed: dict[str, Any]
    signals: tuple[dict[str, Any], ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ReportInput:
    """Всё, что нужно промпту и проверке отчёта за сутки ``day``."""

    day: date
    issued_utc: datetime
    sections: tuple[SectionInput, ...]
    summary: dict[str, Any] | None
    kp_forecasts: tuple[KpForecast, ...]
    forbidden_claims: tuple[str, ...]
    technical_issues: str | None

    def to_payload(self) -> dict[str, Any]:
        """Словарь для промпта и ``check_grounding`` (только JSON-типы)."""
        return {
            "metadata": {
                "issued_utc": self.issued_utc.isoformat(),
                "valid_until_utc": (self.issued_utc + VALIDITY).isoformat(),
                "generator_version": "1.0",
            },
            "day": self.day.isoformat(),
            # готовые строки дат и окно: ``24 hr`` и ``02 Sep`` в тексте иначе не нашлись бы во входе
            "window_hours": int(VALIDITY.total_seconds() // 3600),
            "day_text": date_text(self.day),
            "forecast_day_text": date_text(self.day + timedelta(days=1)),
            "sections": [
                {
                    "name": s.name,
                    "observed": s.observed,
                    "signals": list(s.signals),
                    "notes": list(s.notes),
                }
                for s in self.sections
            ],  # fmt: skip
            "summary": self.summary,
            "kp_forecasts": [f.to_json() for f in self.kp_forecasts],
            "kp_forecast_available": any(f.usable for f in self.kp_forecasts),
            "forbidden_claims": list(self.forbidden_claims),
            "technical_issues": self.technical_issues,
        }


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date_text(day: date) -> str:
    """Дата как в текстах NOAA (``02 Sep``); названия месяцев не зависят от локали."""
    return f"{day.day:02d} {_MONTHS[day.month - 1]}"


def time_text(moment: datetime) -> str:
    """Время события как в текстах NOAA: ``31/1516 UTC``."""
    return f"{moment.astimezone(UTC):%d/%H%M} UTC"


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    """Только непустые значения; дробные округлены (длинные хвосты не нужны ни тексту, ни проверке)."""
    return {
        k: round(v, 3) if isinstance(v, float) else v for k, v in values.items() if v is not None
    }


def _plausible(row: FeatureRow, contract: SignalsContract) -> dict[str, Any]:
    """Значения признаков в допустимом диапазоне паспорта.

    Источник кладёт в ряды заполнитель вроде ``-9999.9``; признак вне ``valid_range`` в текст не
    попадает (иначе модель назвала бы заполнитель скоростью ветра).
    """
    values: dict[str, Any] = {}
    for name, value in row.values.items():
        profile = contract.features.get(name)
        valid = profile.valid_range if profile else None
        if valid and isinstance(value, int | float) and not valid[0] <= value <= valid[1]:
            continue
        values[name] = value
    return values


def _flare_rank(flare_class: str) -> tuple[int, float]:
    parsed = parse_flare_class(flare_class)
    return ("ABCMX".index(parsed[0]), parsed[1]) if parsed else (-1, 0.0)


def _solar_activity(payload: UnifiedPayload, values: Mapping[str, Any]) -> dict[str, Any]:
    activity = payload.solar_activity
    flares = sorted(activity.flares_last_24h, key=lambda f: _flare_rank(f.class_), reverse=True)
    regions = sorted(activity.active_regions, key=lambda r: r.area or 0, reverse=True)
    cmes = sorted(activity.cme_list, key=lambda c: c.speed, reverse=True)
    observed = _clean({k: values.get(k) for k in _KEYS["Solar Activity"]})
    observed.update(_clean({"f107": activity.f107, "ssn": activity.ssn}))
    observed["flares_top"] = [
        _clean({"class": f.class_, "time_text": time_text(f.time), "ar": f.ar})
        for f in flares[:TOP_FLARES]
    ]
    observed["regions_top"] = [
        _clean({"noaa": r.noaa_num, "location": r.location, "mcintosh": r.mcintosh,
                "magnetic_class": r.magnetic_class, "area": r.area})
        for r in regions[:TOP_REGIONS]
    ]  # fmt: skip
    observed["cmes_top"] = [  # без earth_directed: см. докстринг модуля
        {"time_text": time_text(c.time), "speed": c.speed, "width": c.width}
        for c in cmes[:TOP_CMES]
    ]
    return observed


def _solar_wind(payload: UnifiedPayload, values: Mapping[str, Any]) -> dict[str, Any]:
    wind = payload.solar_wind
    observed = _clean({k: values.get(k) for k in _KEYS["Solar Wind"]})
    observed.update(_clean({"phi": wind.phi, "bz_max": wind.bz_max, "source": wind.source}))
    return observed


def _geospace(
    payload: UnifiedPayload, values: Mapping[str, Any], day_row: Mapping[str, Any]
) -> dict[str, Any]:
    geo = payload.geospace
    observed = _clean({k: values.get(k) for k in _KEYS["Geospace"]})
    observed.update(_clean({"dst_observed": geo.dst_observed, "level": geo.current_level}))
    observed.update(_clean({"source_geomagnetic_g": payload.forecast.geomagnetic_g}))
    if day_row.get("kp_values"):
        observed["kp_by_3h"] = [round(float(v), 3) for v in day_row["kp_values"]]
    return observed


def _rule_sections(
    rule: SignalRule, contract: SignalsContract, specs: FeaturesContract
) -> set[str]:
    """Разделы, к которым относится сигнал: по группам признаков его условия или ``on_missing``."""
    if isinstance(rule.when, Condition):
        names = rule.when.features()
    else:  # «данных нет»: признаки, чей on_missing указывает на этот сигнал
        names = {
            n
            for n, p in contract.features.items()
            if p.on_missing and p.on_missing.signal == rule.id
        }
    known = specs.by_id()
    return {
        _SECTION_BY_GROUP[known[n].group]
        for n in names
        if n in known and known[n].group in _SECTION_BY_GROUP
    }


def _signal_json(rule: SignalRule) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": rule.id, "severity": rule.severity.value, "kind": rule.kind.value,
        "title": rule.title, "explain": rule.explain,
        "affects": [s.value for s in rule.affects], "forbids_claims": list(rule.forbids_claims),
    }  # fmt: skip
    return {k: v for k, v in data.items() if v not in (None, [], "")}


def assemble_report_input(
    day: date,
    payload: UnifiedPayload,
    day_row: Mapping[str, Any],
    row: FeatureRow,
    selection: SignalSelection,
    forecasts: Sequence[KpForecast],
    contract: SignalsContract,
    specs: FeaturesContract,
) -> ReportInput:
    """Собрать вход отчёта из уже посчитанных частей (без чтения хранилища)."""
    values = _plausible(row, contract)
    observed = {
        "Solar Activity": _solar_activity(payload, values),
        "Energetic Particle": _clean(payload.energetic_particles.model_dump()),
        "Solar Wind": _solar_wind(payload, values),
        "Geospace": _geospace(payload, values, day_row),
    }
    sections = []
    for name in SECTIONS:
        signals = tuple(
            _signal_json(r) for r in selection.fired if name in _rule_sections(r, contract, specs)
        )
        notes = tuple(  # с именем признака: без него оговорка «если true…» ни к чему не привязана
            f"{k}: {p.llm_note}" for k in _KEYS.get(name, ()) if k in observed[name]
            if (p := contract.features.get(k)) and p.llm_note
        )  # fmt: skip
        sections.append(
            SectionInput(name=name, observed=observed[name], signals=signals, notes=notes)
        )

    summary = selection.summary
    claims = dict.fromkeys(c for r in selection.fired for c in r.forbids_claims)
    return ReportInput(
        day=day,
        issued_utc=end_of_day(day) + ISSUE_OFFSET,
        sections=tuple(sections),
        summary=None
        if summary is None
        else {
            "id": summary.id,
            "explain": summary.explain,
            "unaffected": [s.value for s in summary.unaffected],
        },  # fmt: skip
        kp_forecasts=tuple(forecasts),
        forbidden_claims=tuple(claims),
        technical_issues=payload.technical_issues,
    )


def build_report_input(
    day: date,
    raw_store: ObjectStore,
    *,
    specs: FeaturesContract | None = None,
    contract: SignalsContract | None = None,
    forecasters: Sequence[KpForecaster] | None = None,
) -> ReportInput:
    """Вход отчёта за сутки ``day`` из сырого слоя (без сети).

    Raises:
        ReportInputError: если за сутки нет тела ``/data`` или оно не разбирается.
    """
    specs = specs or load_feature_specs()
    contract = contract or load_signals()
    history = history_from_raw(raw_store, lookback_days(day))
    if day not in history.rows:
        reason = dict(history.gaps).get(day, "нет данных")
        raise ReportInputError(f"за сутки {day} отчёт не собрать: {reason}")
    try:
        payload = payload_from_response(load_data_response(raw_store, day, data_key(day)))
    except (FunctionError, SwxExtractorError) as exc:
        raise ReportInputError(f"за сутки {day} ответ не разбирается: {exc}") from exc

    row = compute_day_features(day, history.rows, specs)
    issued = end_of_day(day) + ISSUE_OFFSET
    models = default_kp_forecasters() if forecasters is None else forecasters
    forecasts = run_forecasters(models, history.rows, issued)
    selection = select_signals(contract, row)
    return assemble_report_input(
        day, payload, history.rows[day], row, selection, forecasts, contract, specs
    )
