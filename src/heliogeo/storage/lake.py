"""Запись и чтение разделов L1 в Parquet.

Схема файла выводится из ``heliogeo.schema.l1``: одно описание столбцов служит и проверке строк, и
типам Parquet, поэтому они не расходятся. Перед записью строки проверяются ``validate_rows``;
строка, нарушающая схему, в озеро не попадает.

Раздел — это один файл ``l1/{сущность}/dt={дата}/part-000.parquet``. Пустой раздел (за сутки не было
ни одной вспышки) тоже записывается пустым файлом: так «сутки обработаны, строк нет» отличается от
«сутки не обработаны». Запись детерминирована: те же строки дают те же байты, повтор перезаписывает
объект, а не плодит дубли.
"""

import io
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from heliogeo.schema.l1 import L1_SCHEMAS, ColumnType, validate_rows
from heliogeo.storage.keys import key_l1
from heliogeo.storage.objects import ObjectNotFoundError, ObjectStore, StoredObject

_ARROW_TYPES: dict[ColumnType, pa.DataType] = {
    ColumnType.INT: pa.int64(),
    ColumnType.FLOAT: pa.float64(),
    ColumnType.STR: pa.string(),
    ColumnType.BOOL: pa.bool_(),
    ColumnType.TIMESTAMP: pa.timestamp("us", tz="UTC"),
    ColumnType.DATE: pa.date32(),
    ColumnType.FLOAT_LIST: pa.list_(pa.float64()),
    ColumnType.STR_LIST: pa.list_(pa.string()),
}
# Snappy читают все движки, включая Yandex Query; сжатие подбирать позже, по измеренному объёму.
_COMPRESSION = "snappy"


class PartitionError(ValueError):
    """Строки не соответствуют схеме сущности или файл раздела не соответствует схеме."""


def arrow_schema(entity: str) -> pa.Schema:
    """Схема Parquet сущности L1; порядок и обязательность столбцов — как в ``L1_SCHEMAS``.

    Raises:
        KeyError: если сущности нет в ``L1_SCHEMAS``.
    """
    spec = L1_SCHEMAS[entity]
    return pa.schema(
        [pa.field(c.name, _ARROW_TYPES[c.type], nullable=c.nullable) for c in spec.columns]
    )


def write_partition(
    store: ObjectStore,
    entity: str,
    day: date,
    rows: Sequence[Mapping[str, Any]],
    *,
    part: int = 0,
) -> StoredObject:
    """Записать раздел сущности за сутки.

    Raises:
        PartitionError: если строки не проходят схему сущности (запись не выполняется).
        KeyError: если сущности нет в ``L1_SCHEMAS``.
    """
    problems = validate_rows(entity, rows)
    if problems:
        raise PartitionError(f"{entity} за {day.isoformat()}: " + "; ".join(problems[:5]))
    table = pa.Table.from_pylist(list(rows), schema=arrow_schema(entity))
    sink = io.BytesIO()
    pq.write_table(table, sink, compression=_COMPRESSION)
    return store.put(key_l1(entity, day, part), sink.getvalue())


def read_partition(
    store: ObjectStore, entity: str, day: date, *, part: int = 0
) -> list[dict[str, Any]]:
    """Прочитать раздел как список строк; схема файла сверяется со схемой сущности.

    Raises:
        ObjectNotFoundError: если раздела нет.
        PartitionError: если схема файла не совпадает с ожидаемой (имена, типы, обязательность).
    """
    data = store.get(key_l1(entity, day, part))
    table = pq.read_table(io.BytesIO(data))
    expected = arrow_schema(entity)
    if not table.schema.equals(expected):
        raise PartitionError(
            f"{entity} за {day.isoformat()}: схема файла не совпадает со схемой сущности\n"
            f"файл: {table.schema}\nожидалось: {expected}"
        )
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows


def read_range(
    store: ObjectStore, entity: str, start: date, end: date
) -> tuple[dict[date, list[dict[str, Any]]], list[date]]:
    """Разделы сущности за сутки от ``start`` до ``end`` включительно.

    Дыры не заполняются: сутки без раздела не попадают в словарь и перечислены вторым значением,
    чтобы признак, которому не хватило истории, знал об этом, а не считал по тому, что есть.

    Raises:
        ValueError: если ``end`` раньше ``start``.
        PartitionError: если схема файла одного из разделов не совпадает со схемой сущности.
    """
    if end < start:
        raise ValueError(f"конец периода {end.isoformat()} раньше начала {start.isoformat()}")
    found: dict[date, list[dict[str, Any]]] = {}
    missing: list[date] = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        try:
            found[day] = read_partition(store, entity, day)
        except ObjectNotFoundError:
            missing.append(day)
    return found, missing


def partition_exists(store: ObjectStore, entity: str, day: date, *, part: int = 0) -> bool:
    """Записан ли раздел (в том числе пустой)."""
    return store.head(key_l1(entity, day, part)) is not None


def write_day(
    store: ObjectStore, day: date, rows: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, StoredObject]:
    """Записать разделы всех семи сущностей за сутки.

    Сначала все строки проверяются, и только потом пишется первый файл: сутки не остаются
    записанными наполовину из-за ошибки в последней сущности.

    Raises:
        PartitionError: если строки хотя бы одной сущности не проходят схему или в ``rows``
            не те сущности, что в ``L1_SCHEMAS``.
    """
    if set(rows) != set(L1_SCHEMAS):
        raise PartitionError(f"ожидались сущности {sorted(L1_SCHEMAS)}, получены {sorted(rows)}")
    for entity in L1_SCHEMAS:
        problems = validate_rows(entity, rows[entity])
        if problems:
            raise PartitionError(f"{entity} за {day.isoformat()}: " + "; ".join(problems[:5]))
    return {entity: write_partition(store, entity, day, rows[entity]) for entity in L1_SCHEMAS}


__all__ = [
    "PartitionError",
    "arrow_schema",
    "partition_exists",
    "read_partition",
    "read_range",
    "write_day",
    "write_partition",
]
