"""Типизированные ответы SWX API: ``/data`` и ``/text``.

Клиент возвращает не сырой dict, а объект, в котором уже сделаны обязательные проверки ответа
(форма, времена, неизвестные поля) и данные приведены к моменту запроса. Разбор мягкий: непонятное
поле становится ``None`` и записью в ``problems``, а не падением, потому что источник развивается.
Но тело, которое не похоже на ответ нужного эндпоинта (например, JSON с ошибкой вместо данных),
отвергается сразу: иначе его приняли бы за данные.

В ответе ``/data`` два тела. ``raw`` — как получено. ``data`` — то, что было известно на
``requested_at``: события и продукты прогноза «из будущего» убраны (см. ``availability``). В маппер
и в факты идёт ``data``. Менять ни то ни другое нельзя: объекты не делят данных между собой и с
исходным dict, но это соглашение, а не запрет на уровне языка.
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from heliogeo.extractors.swx.availability import (
    AvailabilityReport,
    filter_known_at,
    parse_event_time,
    parse_issued,
)

# На сколько источник может ответить на момент, отличный от запрошенного, без предупреждения.
_QUERY_TIME_TOLERANCE = timedelta(minutes=1)

_DATA_REQUIRED = ("query_time", "window")
_TEXT_REQUIRED = ("query_time",)

# Ключи ответов, известные по реальным данным (фикстуры 2026-09-01 и 2024-05-11). Неизвестный ключ
# не ошибка, а повод заметить, что источник изменился: он попадает в ``unknown_fields``.
_DATA_KEYS = frozenset(
    {
        "query_time",
        "window",
        "active_regions",
        "flares",
        "cmes",
        "particles",
        "solar_wind",
        "geomag",
        "sources",
        "notes",
    }
)
_DATA_SECTIONS: Mapping[str, frozenset[str]] = {
    "window": frozenset({"spec", "start", "end"}),
    "particles": frozenset({"electron_gt2mev", "proton_gt10mev", "proton_gt100mev", "sep"}),
    "solar_wind": frozenset(
        {"n_hours", "speed", "density", "imf", "kp_obs", "dst_obs", "n_minutes", "source"}
    ),
    "geomag": frozenset(
        {"kp_obs", "kp_est_now", "dst_obs", "dst_min_day", "g_scale_now", "current_level", "fcst"}
    ),
}
_FCST_KEYS = frozenset(
    {"raw_issued", "kp_forecast", "r_scale", "s_scale", "g_scale", "daypre_excerpt"}
)
_DATA_ROWS: Mapping[str, frozenset[str]] = {
    "flares": frozenset({"ar", "begin", "class", "end", "location", "max", "observatory", "radio"}),
    "cmes": frozenset(
        {
            "active_region",
            "catalog",
            "earth_directed",
            "half_angle",
            "id",
            "lat",
            "lon",
            "note",
            "source_location",
            "speed",
            "time",
            "type",
            "width",
        }
    ),
    "active_regions": frozenset(
        {
            "area",
            "carrington_lon",
            "extent",
            "growth",
            "issued",
            "location",
            "magnetic",
            "mcintosh",
            "noaa",
            "observed_date",
            "spots",
        }
    ),
    "sources": frozenset({"bytes", "mtime", "path", "product"}),
}

_TEXT_KEYS = frozenset(
    {
        "query_time",
        "product",
        "kind",
        "cycle",
        "issued",
        "issued_dt",
        "sections",
        "probabilities",
        "a_indices",
        "f107",
        "text",
        "source",
        "available",
    }
)
_TEXT_SOURCE_KEYS = frozenset({"product", "path", "bytes", "mtime"})
_TEXT_AVAILABLE_KEYS = frozenset({"kind", "cycle", "issued", "path"})
# Discussion выходит дважды в сутки, RSGA — старый суточный продукт (даты до марта 2024).
_TEXT_KINDS = frozenset({"discussion", "rsga"})


class SwxResponseError(Exception):
    """Тело ответа не похоже на ответ ожидаемого эндпоинта."""


@dataclass(frozen=True, kw_only=True)
class SourceRef:
    """Файл источника, из которого собран ответ (``sources[]`` в ``/data``, ``source`` в ``/text``).

    Это готовое происхождение значений: из ``path`` и ``mtime`` строится ``Provenance`` фактов.

    Attributes:
        product: продукт (``srs``, ``events``, ``donki_cme``, ``omni_hro_1min`` и т. д.).
        path: путь файла на складе источника.
        size_bytes: размер файла (поле ``bytes`` ответа).
        mtime: время файла на складе (UTC): дата заполнения архива, а не время выпуска продукта.
    """

    product: str
    path: str
    size_bytes: int | None = None
    mtime: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class TextRef:
    """Выпуск текста из списка ``available[]`` ответа ``/text``.

    Attributes:
        kind: ``discussion`` или ``rsga``.
        cycle: цикл выпуска, например ``0030`` или ``1230``.
        issued: время выпуска (UTC).
        path: путь файла на складе источника.
    """

    kind: str
    path: str
    cycle: str | None = None
    issued: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class SwxDataResponse:
    """Ответ ``/data``: числа, события и их происхождение, проверенные и приведённые к моменту запроса.

    Attributes:
        requested_at: запрошенный момент (UTC, секунды отброшены: источнику уходит минута). По
            нему отсекаются события «из будущего».
        window_spec: запрошенное окно (``window``); ``None`` — по умолчанию, сутки UTC.
        no_live: запрещено ли было подмешивание live-данных SWPC.
        raw: тело ответа как получено, до приведения к моменту запроса.
        data: ``raw`` без событий и прогнозов позже ``requested_at``. Вход для маппера и фактов.
        query_time: момент, на который ответил источник; ``None``, если не удалось разобрать.
        window_start: начало окна ответа; ``None``, если не удалось разобрать.
        window_end: конец окна ответа; ``None``, если не удалось разобрать.
        notes: предупреждения самого источника (поле ``notes``).
        sources: файлы источника, из которых собран ответ.
        window_matches_request: совпадает ли окно ответа с запрошенным; ``None`` — не проверялось.
        availability: что убрано при приведении к моменту запроса.
        raw_sha256: хэш тела ответа; ``None``, пока ответ не сохраняется в зеркале.
        raw_key: ключ сохранённого тела в зеркале; ``None``, пока ответ не сохраняется.
        fetched_at: когда ответ получен (UTC); для ответа из зеркала — когда он был получен впервые.
        from_mirror: взят ли ответ из зеркала, а не из сети.
        unknown_fields: поля ответа, которых нет в известном наборе (например, ``flares[].new``).
        problems: что в ответе показалось неправильным: непонятные поля и времена, расхождение
            моментов. Разбор при этом не прерывается.
    """

    requested_at: datetime
    window_spec: str | None = None
    no_live: bool = False
    raw: Mapping[str, Any]
    data: Mapping[str, Any]
    query_time: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    notes: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    window_matches_request: bool | None = None
    availability: AvailabilityReport
    raw_sha256: str | None = None
    raw_key: str | None = None
    fetched_at: datetime
    from_mirror: bool = False
    unknown_fields: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def events_after_query(self) -> int:
        """Сколько событий (вспышек и выбросов) оказалось позже запрошенного момента и убрано."""
        return self.availability.dropped_events

    @classmethod
    def from_raw(
        cls,
        raw: Mapping[str, Any],
        *,
        requested_at: datetime,
        fetched_at: datetime,
        window_spec: str | None = None,
        no_live: bool = False,
        raw_sha256: str | None = None,
        raw_key: str | None = None,
        from_mirror: bool = False,
    ) -> Self:
        """Собрать ответ из разобранного тела ``/data``. Чистая функция: ни сети, ни диска.

        Raises:
            SwxResponseError: если тело не похоже на ответ ``/data`` (не объект, нет
                ``query_time`` или ``window``).
            ValueError: если ``requested_at`` или ``fetched_at`` без часового пояса.
        """
        requested = _floor_to_minute(requested_at, "requested_at")
        fetched = _to_utc(fetched_at, "fetched_at")
        body = _require_object(raw, _DATA_REQUIRED, "/data")
        problems: list[str] = []

        query_time = _parse_time(body.get("query_time"), "query_time", problems, required=True)
        _check_query_time(query_time, requested, problems)
        window_start, window_end = _parse_window(body, problems)
        notes = _parse_notes(body, problems)
        sources = _parse_sources(body, problems)
        data, availability = filter_known_at(body, requested)

        return cls(
            requested_at=requested,
            window_spec=window_spec,
            no_live=no_live,
            raw=copy.deepcopy(dict(body)),
            data=data,
            query_time=query_time,
            window_start=window_start,
            window_end=window_end,
            notes=notes,
            sources=sources,
            availability=availability,
            raw_sha256=raw_sha256,
            raw_key=raw_key,
            fetched_at=fetched,
            from_mirror=from_mirror,
            unknown_fields=_unknown_data_fields(body),
            problems=tuple(problems),
        )


@dataclass(frozen=True, kw_only=True)
class SwxTextResponse:
    """Ответ ``/text``: эталонный текст NOAA и список доступных выпусков.

    Берётся последний выпуск не позже запрошенного момента. Discussion выходит дважды в сутки
    (00:30 и 12:30 UTC), RSGA — раз в сутки около 22:00.

    Attributes:
        requested_at: запрошенный момент (UTC, секунды отброшены).
        no_live: запрещено ли было подмешивание live-данных.
        raw: тело ответа как получено.
        query_time: момент, на который ответил источник; ``None``, если не удалось разобрать.
        product: продукт, например ``forecast_discussion``.
        kind: ``discussion`` или ``rsga``.
        cycle: цикл выпуска, например ``0030``.
        issued: время выпуска как строка источника (``2026 Sep 01 0030 UTC``).
        issued_dt: время выпуска (UTC); от него отсчитывается период, который описывает текст.
        sections: разделы текста по названиям; внутри ``text``, ``24 hr Summary``, ``Forecast``.
        text: полный сырой текст продукта.
        probabilities: вероятности RSGA; для discussion ``None``. Структура не разбирается.
        a_indices: A-индексы RSGA; для discussion ``None``. Структура не разбирается.
        f107: поток F10.7 из RSGA; для discussion ``None``. Структура не разбирается.
        source: файл источника, из которого взят текст.
        available: все выпуски не позже запрошенного момента; по ним обходят архив.
        raw_sha256: хэш тела ответа; ``None``, пока ответ не сохраняется в зеркале.
        raw_key: ключ сохранённого тела в зеркале; ``None``, пока ответ не сохраняется.
        fetched_at: когда ответ получен (UTC).
        from_mirror: взят ли ответ из зеркала, а не из сети.
        unknown_fields: поля ответа, которых нет в известном наборе.
        problems: что в ответе показалось неправильным; разбор при этом не прерывается.
    """

    requested_at: datetime
    no_live: bool = False
    raw: Mapping[str, Any]
    query_time: datetime | None
    product: str | None = None
    kind: str | None = None
    cycle: str | None = None
    issued: str | None = None
    issued_dt: datetime | None = None
    sections: Mapping[str, Mapping[str, str]]
    text: str | None = None
    probabilities: Any | None = None
    a_indices: Any | None = None
    f107: Any | None = None
    source: SourceRef | None = None
    available: tuple[TextRef, ...] = ()
    raw_sha256: str | None = None
    raw_key: str | None = None
    fetched_at: datetime
    from_mirror: bool = False
    unknown_fields: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @classmethod
    def from_raw(
        cls,
        raw: Mapping[str, Any],
        *,
        requested_at: datetime,
        fetched_at: datetime,
        no_live: bool = False,
        raw_sha256: str | None = None,
        raw_key: str | None = None,
        from_mirror: bool = False,
    ) -> Self:
        """Собрать ответ из разобранного тела ``/text``. Чистая функция: ни сети, ни диска.

        Raises:
            SwxResponseError: если тело не похоже на ответ ``/text`` (не объект, нет
                ``query_time``).
            ValueError: если ``requested_at`` или ``fetched_at`` без часового пояса.
        """
        requested = _floor_to_minute(requested_at, "requested_at")
        fetched = _to_utc(fetched_at, "fetched_at")
        body = _require_object(raw, _TEXT_REQUIRED, "/text")
        problems: list[str] = []

        query_time = _parse_time(body.get("query_time"), "query_time", problems, required=True)
        _check_query_time(query_time, requested, problems)
        kind = _optional_text(body, "kind", problems)
        if kind is not None and kind not in _TEXT_KINDS:
            problems.append(f"unknown text kind: {kind!r}")
        issued = _optional_text(body, "issued", problems)
        issued_dt = _parse_time(body.get("issued_dt"), "issued_dt", problems)
        _check_issued(issued, issued_dt, problems)
        source = None
        if body.get("source") is not None:
            source = _parse_source(body["source"], "source", problems)

        return cls(
            requested_at=requested,
            no_live=no_live,
            raw=copy.deepcopy(dict(body)),
            query_time=query_time,
            product=_optional_text(body, "product", problems),
            kind=kind,
            cycle=_optional_text(body, "cycle", problems),
            issued=issued,
            issued_dt=issued_dt,
            sections=_parse_sections(body, problems),
            text=_optional_text(body, "text", problems),
            probabilities=copy.deepcopy(body.get("probabilities")),
            a_indices=copy.deepcopy(body.get("a_indices")),
            f107=copy.deepcopy(body.get("f107")),
            source=source,
            available=_parse_available(body, problems),
            raw_sha256=raw_sha256,
            raw_key=raw_key,
            fetched_at=fetched,
            from_mirror=from_mirror,
            unknown_fields=_unknown_text_fields(body),
            problems=tuple(problems),
        )


# --- Общие проверки и разбор ---


def _to_utc(moment: datetime, name: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} должен быть с часовым поясом, получено {moment!r}")
    return moment.astimezone(UTC)


def _floor_to_minute(moment: datetime, name: str) -> datetime:
    return _to_utc(moment, name).replace(second=0, microsecond=0)


def _require_object(raw: object, required: Sequence[str], endpoint: str) -> Mapping[str, Any]:
    """Тело должно быть объектом с обязательными полями, иначе это не ответ эндпоинта."""
    if not isinstance(raw, Mapping):
        raise SwxResponseError(f"{endpoint} response is not a JSON object: {type(raw).__name__}")
    missing = [key for key in required if key not in raw]
    if missing:
        received = ", ".join(sorted(map(str, raw))[:10]) or "none"
        raise SwxResponseError(
            f"{endpoint} response has no {', '.join(missing)} (keys received: {received})"
        )
    return raw


def _parse_time(
    value: object, label: str, problems: list[str], *, required: bool = False
) -> datetime | None:
    """Время ответа в UTC; неразобранное — ``None`` и запись в ``problems``."""
    if value is None:
        if required:
            problems.append(f"{label} is missing")
        return None
    parsed = parse_event_time(value)
    if parsed is None:
        problems.append(f"{label} is not an ISO 8601 time with a timezone: {value!r}")
    return parsed


def _optional_text(body: Mapping[str, Any], key: str, problems: list[str]) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        problems.append(f"{key} is not a string: {value!r}")
        return None
    return value


def _check_query_time(
    query_time: datetime | None, requested_at: datetime, problems: list[str]
) -> None:
    """Источник мог проигнорировать время в запросе: тогда агрегаты не на тот момент."""
    if query_time is not None and abs(query_time - requested_at) > _QUERY_TIME_TOLERANCE:
        problems.append(
            f"source answered for query_time={query_time.isoformat()}, "
            f"but {requested_at.isoformat()} was requested"
        )


def _parse_source(entry: object, label: str, problems: list[str]) -> SourceRef | None:
    if not isinstance(entry, Mapping):
        problems.append(f"{label} is not an object: {entry!r}")
        return None
    product, path = entry.get("product"), entry.get("path")
    if not isinstance(product, str) or not isinstance(path, str):
        problems.append(f"{label} has no product or path (keys: {', '.join(sorted(entry))})")
        return None
    size = entry.get("bytes")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int)):
        problems.append(f"{label}.bytes is not an integer: {size!r}")
        size = None
    mtime = _parse_time(entry.get("mtime"), f"{label}.mtime", problems)
    return SourceRef(product=product, path=path, size_bytes=size, mtime=mtime)


def _as_list(value: object, label: str, problems: list[str]) -> list[Any]:
    """Список записей; ``None`` — пустой список, не список — запись в ``problems``."""
    if value is None:
        return []
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        problems.append(f"{label} is not a list: {value!r}")
        return []
    return list(value)


# --- Разбор /data ---


def _parse_window(
    body: Mapping[str, Any], problems: list[str]
) -> tuple[datetime | None, datetime | None]:
    window = body.get("window")
    if not isinstance(window, Mapping):
        problems.append(f"window is not an object: {window!r}")
        return (None, None)
    start = _parse_time(window.get("start"), "window.start", problems, required=True)
    end = _parse_time(window.get("end"), "window.end", problems, required=True)
    return (start, end)


def _parse_notes(body: Mapping[str, Any], problems: list[str]) -> tuple[str, ...]:
    notes: list[str] = []
    for index, note in enumerate(_as_list(body.get("notes"), "notes", problems)):
        if isinstance(note, str):
            notes.append(note)
        else:
            problems.append(f"notes[{index}] is not a string: {note!r}")
    return tuple(notes)


def _parse_sources(body: Mapping[str, Any], problems: list[str]) -> tuple[SourceRef, ...]:
    sources: list[SourceRef] = []
    for index, entry in enumerate(_as_list(body.get("sources"), "sources", problems)):
        ref = _parse_source(entry, f"sources[{index}]", problems)
        if ref is not None:
            sources.append(ref)
    return tuple(sources)


def _extra_keys(value: object, known: frozenset[str]) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return sorted(str(key) for key in value if key not in known)


def _extra_row_keys(rows: object, known: frozenset[str]) -> list[str]:
    if isinstance(rows, str | bytes) or not isinstance(rows, Sequence):
        return []
    present = {str(key) for row in rows if isinstance(row, Mapping) for key in row}
    return sorted(present - known)


def _unknown_data_fields(body: Mapping[str, Any]) -> tuple[str, ...]:
    """Поля ``/data``, которых нет в известном наборе: верхний уровень, разделы, записи списков."""
    found = [str(key) for key in body if key not in _DATA_KEYS]
    for section, known in _DATA_SECTIONS.items():
        found += [f"{section}.{key}" for key in _extra_keys(body.get(section), known)]
    geomag = body.get("geomag")
    if isinstance(geomag, Mapping):
        found += [f"geomag.fcst.{key}" for key in _extra_keys(geomag.get("fcst"), _FCST_KEYS)]
    for rows_key, known in _DATA_ROWS.items():
        found += [f"{rows_key}[].{key}" for key in _extra_row_keys(body.get(rows_key), known)]
    return tuple(sorted(found))


# --- Разбор /text ---


def _check_issued(issued: str | None, issued_dt: datetime | None, problems: list[str]) -> None:
    """Две записи времени выпуска (строка источника и ISO) должны означать один момент."""
    parsed = parse_issued(issued)
    if parsed is not None and issued_dt is not None and parsed != issued_dt:
        problems.append(
            f"issued {issued!r} and issued_dt {issued_dt.isoformat()} are different moments"
        )


def _parse_sections(body: Mapping[str, Any], problems: list[str]) -> dict[str, dict[str, str]]:
    value = body.get("sections")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        problems.append(f"sections is not an object: {type(value).__name__}")
        return {}
    sections: dict[str, dict[str, str]] = {}
    for name, content in value.items():
        if not isinstance(content, Mapping):
            problems.append(f"sections[{name!r}] is not an object")
            continue
        parts: dict[str, str] = {}
        for key, text in content.items():
            if isinstance(text, str):
                parts[str(key)] = text
            else:
                problems.append(f"sections[{name!r}][{key!r}] is not a string")
        sections[str(name)] = parts
    return sections


def _parse_available(body: Mapping[str, Any], problems: list[str]) -> tuple[TextRef, ...]:
    refs: list[TextRef] = []
    for index, entry in enumerate(_as_list(body.get("available"), "available", problems)):
        label = f"available[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{label} is not an object: {entry!r}")
            continue
        kind, path = entry.get("kind"), entry.get("path")
        if not isinstance(kind, str) or not isinstance(path, str):
            problems.append(f"{label} has no kind or path (keys: {', '.join(sorted(entry))})")
            continue
        cycle = entry.get("cycle")
        if cycle is not None and not isinstance(cycle, str):
            problems.append(f"{label}.cycle is not a string: {cycle!r}")
            cycle = None
        issued = _parse_time(entry.get("issued"), f"{label}.issued", problems)
        refs.append(TextRef(kind=kind, path=path, cycle=cycle, issued=issued))
    return tuple(refs)


def _unknown_text_fields(body: Mapping[str, Any]) -> tuple[str, ...]:
    found = [str(key) for key in body if key not in _TEXT_KEYS]
    found += [f"source.{key}" for key in _extra_keys(body.get("source"), _TEXT_SOURCE_KEYS)]
    found += [
        f"available[].{key}" for key in _extra_row_keys(body.get("available"), _TEXT_AVAILABLE_KEYS)
    ]
    return tuple(sorted(found))
