"""Строка признаков на момент ``t`` по контракту ``contracts/features/specs.yaml`` (задача B3-01).

Правило «без данных из будущего»: признак с задержкой ``available_delay`` на момент ``t`` строится
только по данным, известным к ``cutoff = t - available_delay``.

* Трёхчасовое значение Kp известно с конца своего интервала: берутся интервалы, закончившиеся не
  позже ``cutoff`` (границу округляем вниз до трёхчасовой сетки). Окна «за N суток» заканчиваются
  там же, поэтому они точные.
* Остальные суточные величины приходят строками ``observation_daily``, то есть целыми сутками UTC.
  Сутки известны с их конца, поэтому берутся последние сутки, закончившиеся не позже ``cutoff``:
  это ``date(cutoff) - 1``. Если их строки нет, значение отсутствует, а более старые сутки не
  подставляются: устаревшее значение хуже пропуска. Окно «24 часа» здесь равно этим суткам, а не
  скользящему окну, заканчивающемуся ровно в ``cutoff``.

Результат — ``FeatureRow``: значения и статус качества каждого признака (``ok`` — окно полное,
``degraded`` — окно неполное или в исходных данных была оговорка, ``missing`` — значения нет).
«Нет значения» — это ``None``, а не ноль; для максимумов это включает и сутки без событий.

Какие признаки считаются, решает контракт (``specs.features``); признак, для которого здесь нет
вычислителя, — ошибка, а не молчаливый пропуск. Статусы ``planned``/``active`` в контракте на
расчёт не влияют: нужное подмножество задаётся аргументом ``ids``.
"""

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from heliogeo.contracts.features import FeaturesContract, load_feature_specs
from heliogeo.features.daily import (
    KP_PER_DAY,
    MCINTOSH_Z_CLASSES,
    QC_DEGRADED,
    QC_MISSING,
    QC_OK,
    as_number,
    parse_flare_class,
)

FeatureValue = float | int | str | bool | None
DailyRow = Mapping[str, Any]
History = Mapping[date, DailyRow]
Computed = tuple[FeatureValue, str]

_SLOT = timedelta(hours=3)
_DAY = timedelta(days=1)

# Периоды повторяемости (признаки kp_recurrence_*): оборот Солнца и два оборота
RECURRENCE_27D = timedelta(days=27.2753)
RECURRENCE_54D = timedelta(days=54.55)
RECURRENCE_HALF_WINDOW = _DAY  # «окно ±1 сутки»

# Мощность вспышки по рентгеновскому потоку: класс X1.0 — 1e-4 Вт/м², M1.0 — 1e-5 и так далее
_FLARE_FLUX_BASE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}

# Порядок классов McIntosh Z по возрастанию (A, B, C, D, E, F, H)
_MCINTOSH_ORDER = "ABCDEFH"

# (!) cycle_proxy: формулы в контракте нет, написано только «по числу и площади активных областей».
# Это предварительная оценка: среднее за оборот Солнца от доли числа и доли площади областей
# относительно опорных значений. Опорные значения и сама формула требуют подтверждения экспертом.
CYCLE_WINDOW_DAYS = 27
CYCLE_MIN_DAYS = 7
CYCLE_REF_AR_COUNT = 15.0
CYCLE_REF_AR_AREA = 2000.0

# Нижние границы Kp для шкал G1…G5 (Kp приходит третями: 5.67 — всё ещё G1)
_G_MAX_LEVEL = 5
_G_FIRST_KP = 5


@dataclass(frozen=True)
class SwpcForecast:
    """Прогноз SWPC: момент выпуска и числовая сетка Kp (8 интервалов × 3 суток)."""

    issued_utc: datetime
    kp_grid: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.issued_utc.tzinfo is None:
            raise ValueError("issued_utc должно быть с часовым поясом")


@dataclass(frozen=True)
class FeatureRow:
    """Строка признаков на момент ``t``: значения и качество по каждому признаку."""

    t: datetime
    feature_set_v: str
    values: Mapping[str, FeatureValue]
    quality: Mapping[str, str]


@dataclass(frozen=True)
class _Context:
    t: datetime
    history: History
    forecasts: Sequence[SwpcForecast]


Computer = Callable[[_Context, datetime], Computed]

_MISSING: Computed = (None, QC_MISSING)


# --- Общие помощники ---


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _floor_to_slot(moment: datetime) -> datetime:
    return datetime(moment.year, moment.month, moment.day, moment.hour // 3 * 3, tzinfo=UTC)


def _last_complete_day(cutoff: datetime) -> date:
    """Последние сутки UTC, закончившиеся не позже ``cutoff``."""
    return cutoff.date() - _DAY


def _kp_slots(history: History) -> list[tuple[datetime, float]]:
    """Трёхчасовой ряд Kp (начало интервала, значение) из ``kp_values`` строк истории.

    Сутки, где значений не 8, пропускаются: по неполному списку нельзя понять, каким интервалам
    суток они принадлежат.
    """
    slots: list[tuple[datetime, float]] = []
    for day, row in history.items():
        values = row.get("kp_values")
        if not isinstance(values, Sequence) or isinstance(values, str):
            continue
        if len(values) != KP_PER_DAY:
            continue
        start = _day_start(day)
        for index, raw in enumerate(values):
            value = as_number(raw)
            if value is not None:
                slots.append((start + index * _SLOT, value))
    slots.sort()
    return slots


def _window_quality(count: int, expected: int) -> str:
    if count == 0:
        return QC_MISSING
    return QC_OK if count >= expected else QC_DEGRADED


# --- Kp: история и рекуррентность ---


def _kp_cur(ctx: _Context, cutoff: datetime) -> Computed:
    end = _floor_to_slot(cutoff)
    for start, value in _kp_slots(ctx.history):
        if start + _SLOT == end:
            return value, QC_OK
    return _MISSING


def _kp_window(ctx: _Context, cutoff: datetime, days: int) -> tuple[list[float], list[float]]:
    """Значения Kp окна за ``days`` суток и их моменты (в сутках от начала окна) для тренда."""
    end = _floor_to_slot(cutoff)
    begin = end - days * _DAY
    chosen = [(s, v) for s, v in _kp_slots(ctx.history) if begin <= s and s + _SLOT <= end]
    return [v for _, v in chosen], [(s - begin) / _DAY for s, _ in chosen]


def _kp_stat(days: int, reduce: Callable[[list[float]], float]) -> Computer:
    def compute(ctx: _Context, cutoff: datetime) -> Computed:
        values, _ = _kp_window(ctx, cutoff, days)
        if not values:
            return _MISSING
        return reduce(values), _window_quality(len(values), days * KP_PER_DAY)

    return compute


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _kp_trend_3d(ctx: _Context, cutoff: datetime) -> Computed:
    """Наклон прямой наименьших квадратов по Kp за 3 суток, единиц Kp в сутки.

    В контракте сказано только «тренд»; наклон — предварительный выбор, подтвердить у эксперта.
    """
    values, days = _kp_window(ctx, cutoff, 3)
    if len(values) < 2:
        return _MISSING
    mean_x, mean_y = _mean(days), _mean(values)
    spread = sum((x - mean_x) ** 2 for x in days)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(days, values, strict=True)) / spread
    return slope, _window_quality(len(values), 3 * KP_PER_DAY)


def _kp_recurrence(period: timedelta) -> Computer:
    def compute(ctx: _Context, cutoff: datetime) -> Computed:
        center = ctx.t - period
        end = _floor_to_slot(cutoff)
        values = [
            value
            for start, value in _kp_slots(ctx.history)
            if start + _SLOT <= end and abs((start + _SLOT / 2) - center) <= RECURRENCE_HALF_WINDOW
        ]
        if not values:
            return _MISSING
        expected = int(2 * RECURRENCE_HALF_WINDOW / _SLOT)
        return max(values), _window_quality(len(values), expected)

    return compute


# --- Величины из суточных строк ---


def _from_day(group: str, pick: Callable[[DailyRow], FeatureValue]) -> Computer:
    """Значение из строки последних завершённых суток; качество берётся из ``qc[group]``."""

    def compute(ctx: _Context, cutoff: datetime) -> Computed:
        row = ctx.history.get(_last_complete_day(cutoff))
        if row is None:
            return _MISSING
        value = pick(row)
        if value is None:
            return _MISSING
        status = (row.get("qc") or {}).get(group, QC_OK)
        return value, status if status in (QC_OK, QC_DEGRADED) else QC_DEGRADED

    return compute


def _field(name: str) -> Callable[[DailyRow], FeatureValue]:
    return lambda row: row.get(name)


def _flare_count(letter: str) -> Callable[[DailyRow], FeatureValue]:
    return lambda row: (row.get("flare_counts") or {}).get(letter)


def _flare_flux(row: DailyRow) -> FeatureValue:
    """Рентгеновский поток самой мощной вспышки суток, Вт/м²; ``None``, если вспышек не было."""
    parsed = parse_flare_class(row.get("flare_max_class"))
    if parsed is None:
        return None
    letter, magnitude = parsed
    return _FLARE_FLUX_BASE[letter] * magnitude


def _ar_summary_part(row: DailyRow, part: str) -> Mapping[str, int] | None:
    summary = row.get("ar_summary")
    if not isinstance(summary, Mapping):
        return None
    value = summary.get(part)
    return value if isinstance(value, Mapping) else None


def _ar_max_mcintosh_z(row: DailyRow) -> FeatureValue:
    classes = _ar_summary_part(row, "mcintosh_z") or {}
    known = [z for z in classes if z in MCINTOSH_Z_CLASSES]
    return max(known, key=_MCINTOSH_ORDER.index) if known else None


def _ar_new_count(row: DailyRow) -> FeatureValue:
    growth = _ar_summary_part(row, "growth")
    return None if growth is None else growth.get("new", 0)


def _sw_speed_trend_24h(ctx: _Context, cutoff: datetime) -> Computed:
    """Средняя скорость последних суток минус средняя скорость суток перед ними, км/с."""
    last = _last_complete_day(cutoff)
    now, before = ctx.history.get(last), ctx.history.get(last - _DAY)
    if now is None or before is None:
        return _MISSING
    speed_now = as_number(now.get("sw_speed_avg"))
    speed_before = as_number(before.get("sw_speed_avg"))
    if speed_now is None or speed_before is None:
        return _MISSING
    qc = [(r.get("qc") or {}).get("solar_wind", QC_OK) for r in (now, before)]
    return speed_now - speed_before, QC_OK if qc == [QC_OK, QC_OK] else QC_DEGRADED


def _days_since_last(min_classes: Collection[str]) -> Computer:
    """Число суток с последней вспышки указанных классов (на суточном разрешении).

    Считается от последних завершённых суток: 0 — вспышка была в них. Если в истории вспышек
    такого класса нет, значения нет (``None``), а не «давно»: история конечна. Качество
    ``degraded``, если между вспышкой и последними сутками есть сутки без данных по вспышкам.
    """

    def compute(ctx: _Context, cutoff: datetime) -> Computed:
        last = _last_complete_day(cutoff)
        for day in sorted((d for d in ctx.history if d <= last), reverse=True):
            counts = ctx.history[day].get("flare_counts") or {}
            if sum(counts.get(letter, 0) for letter in min_classes) > 0:
                gap_free = all(
                    (ctx.history.get(last - timedelta(days=n)) or {}).get("flare_counts")
                    is not None
                    for n in range((last - day).days + 1)
                )
                return (last - day).days, QC_OK if gap_free else QC_DEGRADED
        return _MISSING

    return compute


# --- Календарь и фаза цикла ---


def _doy(ctx: _Context, cutoff: datetime) -> Computed:
    return ctx.t.timetuple().tm_yday, QC_OK


def _cycle_proxy(ctx: _Context, cutoff: datetime) -> Computed:
    """Предварительная оценка уровня активности цикла (0–1); формула — см. CYCLE_REF_*."""
    last = _last_complete_day(cutoff)
    shares: list[float] = []
    for n in range(CYCLE_WINDOW_DAYS):
        row = ctx.history.get(last - timedelta(days=n))
        if row is None:
            continue
        count, area = as_number(row.get("ar_count")), as_number(row.get("ar_total_area"))
        if count is None or area is None:
            continue
        shares.append(
            (min(count / CYCLE_REF_AR_COUNT, 1.0) + min(area / CYCLE_REF_AR_AREA, 1.0)) / 2
        )
    if len(shares) < CYCLE_MIN_DAYS:
        return _MISSING
    return _mean(shares), _window_quality(len(shares), CYCLE_WINDOW_DAYS)


# --- Чужой прогноз SWPC ---


def _swpc_g_scale(ctx: _Context, cutoff: datetime) -> Computed:
    """Уровень G (0–5) по максимуму числовой сетки Kp последнего прогноза, выпущенного до t.

    Поле ``g_scale`` источника не используется: оно берётся из первого вхождения «G1…G5» в тексте
    («No G1 … are expected» даёт ложное G1). Нет сетки — нет значения, не ноль.
    """
    issued = [f for f in ctx.forecasts if f.issued_utc <= cutoff]
    if not issued:
        return _MISSING
    latest = max(issued, key=lambda f: f.issued_utc)
    grid = [v for v in map(as_number, latest.kp_grid) if v is not None]
    if not grid:
        return _MISSING
    level = max(0, min(_G_MAX_LEVEL, math.floor(max(grid)) - _G_FIRST_KP + 1))
    return level, QC_OK


def _swpc_r_scale(ctx: _Context, cutoff: datetime) -> Computed:
    """Не считается: надёжного источника значения нет.

    Источник берёт шкалы R и S тем же поиском первого вхождения по тексту, что и G, но числовой
    сетки для них нет. Чтение таблицы вероятностей по дням ещё не написано, а подставлять
    ложное «R1» из фразы «No R1 … are expected» нельзя.
    """
    return _MISSING


# --- Реестр вычислителей ---

COMPUTERS: dict[str, Computer] = {
    # activity_history
    "kp_cur": _kp_cur,
    "kp_max_1d": _kp_stat(1, max),
    "kp_mean_1d": _kp_stat(1, _mean),
    "kp_max_3d": _kp_stat(3, max),
    "kp_mean_3d": _kp_stat(3, _mean),
    "kp_max_7d": _kp_stat(7, max),
    "kp_mean_7d": _kp_stat(7, _mean),
    "kp_trend_3d": _kp_trend_3d,
    "dst_min_1d": _from_day("dst", _field("dst_min")),
    # recurrence
    "kp_recurrence_27d": _kp_recurrence(RECURRENCE_27D),
    "kp_recurrence_54d": _kp_recurrence(RECURRENCE_54D),
    # solar_wind
    "sw_speed_min": _from_day("solar_wind", _field("sw_speed_min")),
    "sw_speed_avg": _from_day("solar_wind", _field("sw_speed_avg")),
    "sw_speed_max": _from_day("solar_wind", _field("sw_speed_max")),
    "sw_density_avg": _from_day("solar_wind", _field("sw_density_avg")),
    "sw_bt": _from_day("solar_wind", _field("sw_bt")),
    "sw_bz_min": _from_day("solar_wind", _field("sw_bz_min")),
    "sw_sector": _from_day("solar_wind", _field("sw_sector")),
    "sw_speed_trend_24h": _sw_speed_trend_24h,
    # solar_activity
    "ar_count": _from_day("active_regions", _field("ar_count")),
    "ar_total_area": _from_day("active_regions", _field("ar_total_area")),
    "ar_has_delta": _from_day("active_regions", _field("ar_has_delta")),
    "ar_max_mcintosh_z": _from_day("active_regions", _ar_max_mcintosh_z),
    "ar_new_count": _from_day("active_regions", _ar_new_count),
    "flares_c_24h": _from_day("flares", _flare_count("C")),
    "flares_m_24h": _from_day("flares", _flare_count("M")),
    "flares_x_24h": _from_day("flares", _flare_count("X")),
    "flare_max_flux_24h": _from_day("flares", _flare_flux),
    "days_since_last_m": _days_since_last(("M", "X")),
    "days_since_last_x": _days_since_last(("X",)),
    # cme
    "cme_count_24h": _from_day("cme", _field("cme_count")),
    "cme_earth_directed": _from_day("cme", _field("cme_earth_directed")),
    "cme_direction_unknown": _from_day("cme", _field("cme_direction_unknown")),
    "cme_speed_max_24h": _from_day("cme", _field("cme_speed_max")),
    "cme_width_max_24h": _from_day("cme", _field("cme_width_max")),
    "cme_earth_directed_speed_max_24h": _from_day("cme", _field("cme_earth_directed_speed_max")),
    # calendar
    "doy": _doy,
    "cycle_proxy": _cycle_proxy,
    # external_forecast
    "swpc_g_scale": _swpc_g_scale,
    "swpc_r_scale": _swpc_r_scale,
}


def compute_features(
    t: datetime,
    history: History,
    specs: FeaturesContract | None = None,
    *,
    forecasts: Sequence[SwpcForecast] = (),
    ids: Collection[str] | None = None,
) -> FeatureRow:
    """Признаки на момент ``t`` по истории суточных наблюдений.

    Args:
        t: момент, на который строится строка (UTC, с часовым поясом).
        history: строки суточных наблюдений по датам (результат ``extract_observation_daily``).
            Данные позже ``t - available_delay`` признакам не доступны, даже если они в истории.
        specs: контракт признаков; по умолчанию читается ``contracts/features/specs.yaml``.
        forecasts: выпуски прогноза SWPC для группы ``external_forecast``.
        ids: посчитать только эти признаки (по умолчанию — все из контракта).

    Raises:
        ValueError: ``t`` без часового пояса; признак, которого нет в контракте; признак из
            контракта, для которого нет вычислителя.
    """
    if t.tzinfo is None:
        raise ValueError("t должно быть с часовым поясом")
    t = t.astimezone(UTC)
    specs = specs or load_feature_specs()
    by_id = specs.by_id()
    wanted = list(by_id) if ids is None else list(ids)
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise ValueError(f"признаков нет в контракте: {', '.join(unknown)}")
    no_computer = [i for i in wanted if i not in COMPUTERS]
    if no_computer:
        raise ValueError(f"нет вычислителя для признаков: {', '.join(no_computer)}")

    ctx = _Context(t=t, history=history, forecasts=forecasts)
    values: dict[str, FeatureValue] = {}
    quality: dict[str, str] = {}
    for feature_id in wanted:
        cutoff = t - by_id[feature_id].available_delay
        values[feature_id], quality[feature_id] = COMPUTERS[feature_id](ctx, cutoff)
    return FeatureRow(t=t, feature_set_v=specs.version, values=values, quality=quality)
