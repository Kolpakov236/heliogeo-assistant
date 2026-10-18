"""Чтение сохранённого тела ответа из бакета ``raw`` обратно в типизированный ответ.

Манифест за дату хранит всё, что нужно для восстановления ответа: момент запроса, окно, режим
live и время получения. Поэтому ответ собирается без сети и без системных часов и совпадает с тем,
что клиент вернул при скачивании (кроме ``from_mirror=True``).
"""

import json
from datetime import UTC, date, datetime, time

from heliogeo.extractors.swx.response import SwxDataResponse, SwxResponseError
from heliogeo.functions.runtime import FunctionError
from heliogeo.mirror.raw import RawEntry, RawStore
from heliogeo.pipeline.pipeline import ErrorClass
from heliogeo.storage.objects import ObjectNotFoundError, ObjectStore


def day_moment(day: date) -> datetime:
    """Момент запроса для полных суток: 23:59 UTC."""
    return datetime.combine(day, time(23, 59), tzinfo=UTC)


def find_entry(raw: RawStore, day: date, key: str) -> RawEntry | None:
    """Запись манифеста о теле с ключом ``key`` или ``None``."""
    return next((e for e in raw.load_manifest(day).entries if e.key == key), None)


def is_stored(raw: RawStore, store: ObjectStore, day: date, key: str) -> bool:
    """Записано ли тело и цел ли объект (хеш объекта совпадает с манифестом)."""
    entry = find_entry(raw, day, key)
    if entry is None:
        return False
    stored = store.head(key)
    return stored is not None and stored.sha256 == entry.sha256


def load_data_response(store: ObjectStore, day: date, key: str) -> SwxDataResponse:
    """Ответ ``/data`` из сохранённого тела.

    Raises:
        FunctionError: ``DATA_INSUFFICIENT``, если тела или записи о нём нет (ingest не принёс
            данных); ``SOURCE_MALFORMED``, если сохранённое тело не разбирается.
    """
    entry = find_entry(RawStore(store), day, key)
    if entry is None:
        raise FunctionError(ErrorClass.DATA_INSUFFICIENT, f"в манифесте за {day} нет {key}")
    try:
        body = store.get(key)
    except ObjectNotFoundError:
        raise FunctionError(
            ErrorClass.DATA_INSUFFICIENT, f"объекта {key} нет в хранилище"
        ) from None
    try:
        return SwxDataResponse.from_raw(
            json.loads(body),
            requested_at=entry.requested_at,
            fetched_at=entry.fetched_at,
            window_spec=entry.window,
            no_live=entry.no_live,
            raw_sha256=entry.sha256,
            raw_key=entry.key,
            from_mirror=True,
        )
    except (ValueError, SwxResponseError) as exc:
        raise FunctionError(ErrorClass.SOURCE_MALFORMED, f"{key} не разбирается: {exc}") from exc
