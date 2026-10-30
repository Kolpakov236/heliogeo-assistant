"""История суточных строк из сырого слоя: для признаков за сутки не нужна сеть.

Для каждых суток берётся сохранённое тело ``/data`` (то, что скачал ``fn-ingest``), приводится к
ответу и разбирается в строку ``observation_daily``. Сутки без тела или с телом, которое не
разбирается, не прерывают чтение: они попадают в ``gaps`` с причиной, а в истории их строки нет. Так
признаки, которым нужны эти сутки, честно получают статус ``missing`` и не подменяются более
старыми значениями.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from heliogeo.features.daily import extract_observation_daily
from heliogeo.functions.runtime import FunctionError
from heliogeo.functions.stored import day_moment, load_data_response
from heliogeo.storage.keys import key_raw_snapshot
from heliogeo.storage.objects import ObjectStore


@dataclass(frozen=True, kw_only=True)
class RawHistory:
    """Строки суточных наблюдений и список суток, которых нет.

    Attributes:
        rows: строки по датам (подходят как ``history`` для ``compute_features``).
        gaps: сутки без строки и причина, по порядку суток.
    """

    rows: dict[date, dict[str, object]] = field(default_factory=dict)
    gaps: tuple[tuple[date, str], ...] = ()


def data_key(day: date) -> str:
    """Ключ тела ``/data`` за полные сутки (так его сохраняет ``fn-ingest``)."""
    return key_raw_snapshot("data", day_moment(day), None, True)


def history_from_raw(raw_store: ObjectStore, days: Iterable[date]) -> RawHistory:
    """Прочитать строки суточных наблюдений за ``days`` из бакета ``raw``."""
    rows: dict[date, dict[str, object]] = {}
    gaps: list[tuple[date, str]] = []
    for day in days:
        key = data_key(day)
        try:
            response = load_data_response(raw_store, day, key)
        except FunctionError as exc:
            gaps.append((day, str(exc)))
            continue
        rows[day] = extract_observation_daily(response.data, d=day, raw_s3_key=key)
    return RawHistory(rows=rows, gaps=tuple(gaps))
