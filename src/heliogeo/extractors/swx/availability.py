"""Что из ответа источника было известно на момент запроса.

Ответ источника за дату покрывает целые сутки UTC, а момент запроса может быть раньше конца суток
(дата без времени — это 12:00 UTC). Поэтому для архивной даты в ответе лежат события «из будущего»
относительно момента запроса. Если их оставить, текст «на 12:00» расскажет о вспышке в 21:10, а
модель прогноза обучится на данных, которых в тот момент не было.

``filter_known_at`` убирает из ответа:

- вспышки, у которых пик (``max``, при его отсутствии ``begin``) позже момента запроса;
- корональные выбросы, у которых ``time`` позже момента запроса;
- продукты прогноза (трёхдневный прогноз и daypre), выпущенные позже момента запроса.

Вспышка, которая идёт в момент запроса (пик уже был, конец ещё нет), остаётся: её класс известен по
пику. Время конца в payload не попадает; такие вспышки перечислены в отчёте.

Агрегаты окна (min/avg/max скорости ветра, Kp, Dst, флюенсы) и таблица активных областей не
фильтруются: из агрегата нельзя вычесть часть окна. Это ограничение описано в docs/data_sources.md.
"""

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

THREE_DAY_FORECAST = "three_day_forecast"
DAYPRE = "daypre"

# Поля fcst, которые принадлежат трёхдневному прогнозу (он выпущен в raw_issued).
_THREE_DAY_KEYS = ("raw_issued", "kp_forecast", "r_scale", "s_scale", "g_scale")

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
_ISSUED = re.compile(
    r"(?P<year>\d{4}) (?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}) (?P<hour>\d{2})(?P<minute>\d{2}) UTC"
)
_DAYPRE_ISSUED = re.compile(r":Issued:\s*(?P<issued>.+?)\s*")


def parse_event_time(value: object) -> datetime | None:
    """Время события из ответа в UTC или ``None``, если разобрать нельзя.

    Источник отдаёт время в двух формах: ``2026-09-01T07:36:00+00:00`` (вспышки) и
    ``2026-09-01T21:24Z`` (выбросы). Время без часового пояса не принимается.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def parse_issued(value: object) -> datetime | None:
    """Время выпуска продукта прогноза (``2026 Sep 01 1230 UTC``) в UTC или ``None``.

    Месяц разбирается по таблице, а не через ``strptime``: ``%b`` зависит от локали.
    """
    if not isinstance(value, str):
        return None
    match = _ISSUED.fullmatch(value.strip())
    if match is None:
        return None
    month = _MONTHS.get(match["month"])
    if month is None:
        return None
    try:
        return datetime(
            int(match["year"]),
            month,
            int(match["day"]),
            int(match["hour"]),
            int(match["minute"]),
            tzinfo=UTC,
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class AvailabilityReport:
    """Что убрано из ответа и что осталось в нём «в процессе» на момент запроса.

    Attributes:
        query_time: момент, к которому приведён ответ (UTC).
        dropped_flares: сколько вспышек отброшено (пик позже момента запроса).
        dropped_cmes: сколько выбросов отброшено.
        dropped_forecasts: продукты прогноза, выпущенные позже момента запроса.
        ongoing_flares: вспышки, идущие в момент запроса: «пик класс», конец не используется.
    """

    query_time: datetime
    dropped_flares: int = 0
    dropped_cmes: int = 0
    dropped_forecasts: tuple[str, ...] = ()
    ongoing_flares: tuple[str, ...] = ()

    @property
    def dropped_events(self) -> int:
        return self.dropped_flares + self.dropped_cmes

    def summary(self) -> str:
        """Одна строка для журнала."""
        parts = [
            f"query_time={self.query_time.isoformat()}",
            f"dropped flares={self.dropped_flares}, CMEs={self.dropped_cmes}",
        ]
        if self.dropped_forecasts:
            parts.append("dropped forecast products: " + ", ".join(self.dropped_forecasts))
        if self.ongoing_flares:
            parts.append("flares in progress: " + ", ".join(self.ongoing_flares))
        return "; ".join(parts)


def filter_known_at(
    data: Mapping[str, Any], query_time: datetime
) -> tuple[dict[str, Any], AvailabilityReport]:
    """Копия ответа ``/data`` без того, чего на ``query_time`` ещё не было.

    Копия полная: она не делит объекты с исходным ``data``, поэтому правка одного не затрагивает
    другой. Записи, у которых время не удалось разобрать, остаются как есть: дальше их проверит
    валидация payload.

    Raises:
        ValueError: если ``query_time`` без часового пояса.
    """
    if query_time.tzinfo is None or query_time.utcoffset() is None:
        raise ValueError(f"query_time должен быть с часовым поясом, получено {query_time!r}")
    cutoff = query_time.astimezone(UTC)
    result = copy.deepcopy(dict(data))

    flares, dropped_flares, ongoing = _filter_flares(result.get("flares"), cutoff)
    cmes, dropped_cmes = _filter_cmes(result.get("cmes"), cutoff)
    if "flares" in result:
        result["flares"] = flares
    if "cmes" in result:
        result["cmes"] = cmes

    dropped_forecasts: tuple[str, ...] = ()
    geomag = result.get("geomag")
    if isinstance(geomag, Mapping) and isinstance(geomag.get("fcst"), Mapping):
        fcst, dropped_forecasts = _filter_forecast(geomag["fcst"], cutoff)
        result["geomag"] = {**geomag, "fcst": fcst}

    report = AvailabilityReport(
        query_time=cutoff,
        dropped_flares=dropped_flares,
        dropped_cmes=dropped_cmes,
        dropped_forecasts=dropped_forecasts,
        ongoing_flares=ongoing,
    )
    return result, report


def _rows(value: object) -> list[Any] | None:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return None


def _flare_peak(flare: object) -> datetime | None:
    """Момент пика: ``max``, при его отсутствии ``begin`` (как и в маппере)."""
    if not isinstance(flare, Mapping):
        return None
    return parse_event_time(flare.get("max") or flare.get("begin"))


def _filter_flares(value: object, cutoff: datetime) -> tuple[Any, int, tuple[str, ...]]:
    rows = _rows(value)
    if rows is None:
        return value, 0, ()
    kept: list[Any] = []
    dropped = 0
    ongoing: list[str] = []
    for flare in rows:
        peak = _flare_peak(flare)
        if peak is not None and peak > cutoff:
            dropped += 1
            continue
        kept.append(flare)
        if peak is not None:
            end = parse_event_time(flare.get("end"))
            if end is not None and end > cutoff:
                ongoing.append(f"{peak.isoformat()} {flare.get('class')}")
    return kept, dropped, tuple(ongoing)


def _filter_cmes(value: object, cutoff: datetime) -> tuple[Any, int]:
    rows = _rows(value)
    if rows is None:
        return value, 0
    kept: list[Any] = []
    dropped = 0
    for cme in rows:
        moment = parse_event_time(cme.get("time")) if isinstance(cme, Mapping) else None
        if moment is not None and moment > cutoff:
            dropped += 1
            continue
        kept.append(cme)
    return kept, dropped


def _daypre_issued(lines: object) -> datetime | None:
    """Время выпуска daypre по строке ``:Issued: 2026 Sep 01 2200 UTC`` выдержки."""
    if isinstance(lines, str) or not isinstance(lines, Sequence):
        return None
    for line in lines:
        if isinstance(line, str):
            match = _DAYPRE_ISSUED.fullmatch(line.strip())
            if match is not None:
                return parse_issued(match["issued"])
    return None


def _filter_forecast(
    fcst: Mapping[str, Any], cutoff: datetime
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Убрать продукты прогноза, выпущенные позже ``cutoff``; каждый проверяется по своему времени."""
    result = dict(fcst)
    dropped: list[str] = []
    issued = parse_issued(fcst.get("raw_issued"))
    if issued is not None and issued > cutoff:
        for key in _THREE_DAY_KEYS:
            result.pop(key, None)
        dropped.append(THREE_DAY_FORECAST)
    daypre_issued = _daypre_issued(fcst.get("daypre_excerpt"))
    if daypre_issued is not None and daypre_issued > cutoff:
        result.pop("daypre_excerpt", None)
        dropped.append(DAYPRE)
    return result, tuple(dropped)
