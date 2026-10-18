"""fn-normalize: сохранённый ответ источника за сутки в Parquet-разделы L1.

Читает из бакета ``raw`` тело ``/data``, строит строки семи сущностей (``normalize_response``) и
пишет разделы в бакет ``lake`` (``write_day``). Запись детерминирована, поэтому повторный вызов за
ту же дату перезаписывает те же объекты теми же байтами.

Вход: ``in.raw_data`` — ключ тела ``/data`` от ``fn-ingest``. Если ingest не принёс ключа (источник
был недоступен и воркфлоу пошёл дальше с деградацией), берётся ключ полных суток ``date``: возможно,
данные уже скачаны прошлым запуском. Нет и их — ``DATA_INSUFFICIENT``.

Выход: ``l1_keys`` — ключи семи разделов по сущностям. Замечания разбора (отброшенные строки, замены
нуля) идут в ``notes``, число строк по сущностям — в ``metrics``.

Классы ошибок: нет данных — ``DATA_INSUFFICIENT``; сохранённое тело не разбирается —
``SOURCE_MALFORMED``; ответ получен раньше конца суток или за другую дату — ``CONFIG_ERROR`` (повтор
не поможет, ошибка в том, как запрошены данные); нарушение схемы при записи — ``INTERNAL`` (после
``normalize_response`` его быть не должно).
"""

from typing import Any

from heliogeo.extractors.swx.normalize import NormalizeError, normalize_response
from heliogeo.functions.runtime import (
    FunctionError,
    FunctionEvent,
    FunctionOutput,
    Implementation,
    run,
)
from heliogeo.functions.stored import day_moment, load_data_response
from heliogeo.pipeline.pipeline import ErrorClass
from heliogeo.storage.factory import open_object_store
from heliogeo.storage.keys import Bucket, key_raw_snapshot
from heliogeo.storage.lake import PartitionError, write_day
from heliogeo.storage.objects import ObjectStore


def make_normalize(raw_store: ObjectStore, lake_store: ObjectStore) -> Implementation:
    """Реализация ``fn-normalize`` для данных хранилищ ``raw`` и ``lake``."""

    def normalize(event: FunctionEvent) -> FunctionOutput:
        default_key = key_raw_snapshot("data", day_moment(event.date), None, True)
        key = str(event.inputs.get("raw_data") or default_key)

        response = load_data_response(raw_store, event.date, key)
        try:
            partition = normalize_response(response, raw_key=key)
        except NormalizeError as exc:
            raise FunctionError(ErrorClass.CONFIG_ERROR, str(exc)) from exc
        if partition.day != event.date:
            raise FunctionError(
                ErrorClass.CONFIG_ERROR,
                f"ответ за {partition.day.isoformat()}, а запуск за {event.date.isoformat()}",
            )
        try:
            stored = write_day(lake_store, partition.day, partition.rows)
        except PartitionError as exc:
            raise FunctionError(ErrorClass.INTERNAL, str(exc)) from exc

        metrics: dict[str, int | float | str | bool] = {
            f"rows_{entity}": len(rows) for entity, rows in partition.rows.items()
        }
        metrics["problems"] = len(partition.problems)
        return FunctionOutput(
            out={"l1_keys": {entity: obj.key for entity, obj in stored.items()}},
            metrics=metrics,
            notes=partition.problems,
        )

    return normalize


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Точка входа Cloud Function ``fn-normalize``."""
    impl = make_normalize(open_object_store(Bucket.RAW), open_object_store(Bucket.LAKE))
    return run(event, impl, step="normalize")
