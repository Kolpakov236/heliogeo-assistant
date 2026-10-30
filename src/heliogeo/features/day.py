"""Признаки за сутки: каждый признак считается на свой момент «конец суток + его задержка».

``compute_features`` строит всё на один момент ``t``, и у каждого признака своя задержка
доступности: на ``t = D+1 00:00`` Kp (задержка 3 часа) ещё описывает вчерашний интервал, а выбросы
(задержка в сутки) отстают на двое суток. Для отчёта «за сутки D» нужно другое: значение каждого
признака именно за сутки D. Поэтому здесь признак считается тогда, когда его данные за сутки D уже
известны, то есть на ``конец D + available_delay``, а результаты склеиваются в одну строку. Данные
из будущего при этом по-прежнему не берутся: у каждого признака своё время и своя граница.

Признаки без задержки (календарь, прогноз SWPC) считаются на последнюю секунду суток D: так
``doy`` — день года самих суток D, а прогноз — последний выпуск, сделанный до конца суток.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from heliogeo.contracts.features import FeaturesContract, load_feature_specs
from heliogeo.features.compute import (
    FeatureRow,
    FeatureValue,
    History,
    SwpcForecast,
    compute_features,
)

# Самое длинное окно признаков — повторяемость через 54,55 суток с окном ±1 сутки: ей нужны
# строки истории на 56 суток назад от суток отчёта.
LOOKBACK_DAYS = 56
_ONE_DAY = timedelta(days=1)
_ONE_SECOND = timedelta(seconds=1)


def lookback_days(day: date) -> list[date]:
    """Сутки, строки которых нужны для признаков за сутки ``day``: от ``LOOKBACK_DAYS`` назад."""
    first = day.toordinal() - LOOKBACK_DAYS
    return [date.fromordinal(n) for n in range(first, day.toordinal() + 1)]


def end_of_day(day: date) -> datetime:
    """Конец суток ``day``: полночь следующих суток (UTC)."""
    return datetime.combine(day + _ONE_DAY, time(0), tzinfo=UTC)


def compute_day_features(
    day: date,
    history: History,
    specs: FeaturesContract | None = None,
    *,
    forecasts: Sequence[SwpcForecast] = (),
) -> FeatureRow:
    """Все признаки контракта за сутки ``day`` в одной строке.

    Args:
        day: сутки UTC, за которые строится отчёт.
        history: строки суточных наблюдений по датам (``features.daily.extract_observation_daily``);
            нужны сами сутки ``day`` и предыдущие (см. ``lookback_days``). Нет строки — признаки,
            которые от неё зависят, получают статус ``missing``, а не значение из более старых суток.
        specs: контракт признаков; по умолчанию ``contracts/features/specs.yaml``.
        forecasts: выпуски прогноза SWPC; берётся последний, сделанный до конца суток.

    Returns:
        Строка признаков с моментом ``t`` = конец суток; качество — по каждому признаку отдельно.
    """
    specs = specs or load_feature_specs()
    end = end_of_day(day)
    values: dict[str, FeatureValue] = {}
    quality: dict[str, str] = {}
    for spec in specs.features:
        anchor = end + spec.available_delay if spec.available_delay else end - _ONE_SECOND
        row = compute_features(anchor, history, specs, forecasts=forecasts, ids=[spec.id])
        values[spec.id] = row.values[spec.id]
        quality[spec.id] = row.quality[spec.id]
    return FeatureRow(t=end, feature_set_v=specs.version, values=values, quality=quality)
