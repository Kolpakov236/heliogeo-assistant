"""fn-ingest: скачать данные источника за сутки и сохранить как получены.

Берутся полные сутки ``date``: запрос на последнюю минуту суток (23:59 UTC), без live-данных — так
ответ воспроизводим, а событий «из будущего» для суток нет. Тело ответа ``/data`` (обязательно) и
``/text`` (по возможности) сохраняется в бакет ``raw`` байтами как получено, рядом ведётся манифест
за дату.

* Повторный вызов за ту же дату не идёт в сеть, если тело ``/data`` уже сохранено и объект цел.
* Отсутствие части продуктов — не отказ: функция отвечает ``ok`` и перечисляет пропуски в ``notes``
  (см. ``check_completeness``). Отказом считается только невозможность получить сам ``/data``.
* Класс ошибки определяется ошибкой клиента: недоступность источника — ``SOURCE_UNAVAILABLE``
  (повтор лечит), нет данных за дату — ``DATA_INSUFFICIENT`` (деградация), тело не похоже на ответ —
  ``SOURCE_MALFORMED`` (повтор не лечит).

Вход: ``in`` не нужен. Выход: ``raw_data`` — ключ тела ``/data``, ``raw_text`` — ключ тела ``/text``
(если получено), ``raw_manifest`` — ключ манифеста за дату.
"""

from collections.abc import Callable
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.completeness import check_completeness
from heliogeo.extractors.swx.extractor import SwxExtractorError, payload_from_response
from heliogeo.extractors.swx.response import SwxDataResponse
from heliogeo.functions.runtime import FunctionEvent, FunctionOutput, Implementation, run
from heliogeo.functions.stored import day_moment, is_stored, load_data_response
from heliogeo.mirror.raw import RawStore
from heliogeo.storage.factory import open_object_store
from heliogeo.storage.keys import Bucket, key_manifest, key_raw_snapshot
from heliogeo.storage.objects import ObjectStore

logger = get_logger(__name__)


def make_ingest(
    raw_store: ObjectStore, client_factory: Callable[[RawStore], SwxClient]
) -> Implementation:
    """Реализация ``fn-ingest`` для данного хранилища сырья.

    Args:
        raw_store: бакет ``raw``.
        client_factory: строит клиента, который сохраняет тела ответов в переданный ``RawStore``.
    """

    def ingest(event: FunctionEvent) -> FunctionOutput:
        raw = RawStore(raw_store)
        moment = day_moment(event.date)
        data_key = key_raw_snapshot("data", moment, None, True)
        text_key = key_raw_snapshot("text", moment, None, True)
        notes: list[str] = []
        metrics: dict[str, int | float | str | bool] = {}

        if is_stored(raw, raw_store, event.date, data_key):
            logger.info("Raw %s is already stored, the source is not asked again", data_key)
            response = load_data_response(raw_store, event.date, data_key)
            metrics["from_raw"] = True
        else:
            response = client_factory(raw).get_data(query_time=moment, window=None, no_live=True)
            metrics["from_raw"] = False
        _assess(response, notes, metrics)

        out: dict[str, Any] = {"raw_data": data_key, "raw_manifest": key_manifest(event.date)}
        if is_stored(raw, raw_store, event.date, text_key):
            out["raw_text"] = text_key
        else:
            try:
                client_factory(raw).get_text(query_time=moment, no_live=True)
                out["raw_text"] = text_key
            except SwxClientError as exc:
                notes.append(f"текст сводки не получен: {exc}")
        return FunctionOutput(out=out, metrics=metrics, notes=tuple(notes))

    return ingest


def _assess(
    response: SwxDataResponse, notes: list[str], metrics: dict[str, int | float | str | bool]
) -> None:
    """Замечания источника, полнота данных и проверка, что из ответа собирается payload."""
    notes.extend(f"источник: {problem}" for problem in response.problems)
    completeness = check_completeness(response)
    notes.extend(completeness.notes())
    metrics["missing_critical"] = len(completeness.critical)
    metrics["missing_important"] = len(completeness.important)
    metrics["missing_products"] = len(completeness.products)
    try:
        payload_from_response(response)
    except SwxExtractorError as exc:
        notes.append(f"из ответа не собирается payload для генерации: {exc}")
        metrics["payload_ok"] = False
    else:
        metrics["payload_ok"] = True


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Точка входа Cloud Function ``fn-ingest``."""

    def client_factory(raw: RawStore) -> SwxClient:
        return SwxClient(raw_sink=raw.save_snapshot)

    return run(event, make_ingest(open_object_store(Bucket.RAW), client_factory), step="ingest")
