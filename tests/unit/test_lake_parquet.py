"""Parquet-разделы L1: схема файла из схемы сущности, запись без потерь, отказ на неверных строках."""

import hashlib
import io
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from heliogeo.extractors.swx.normalize import normalize_response
from heliogeo.schema.l1 import L1_SCHEMAS, ColumnType
from heliogeo.storage.keys import key_l1
from heliogeo.storage.lake import (
    PartitionError,
    arrow_schema,
    partition_exists,
    read_partition,
    read_range,
    write_day,
    write_partition,
)
from heliogeo.storage.objects import LocalObjectStore, ObjectNotFoundError
from tests.mocks.l1 import make_l1_row
from tests.mocks.swx import QUIET_DATA, QUIET_END_OF_DAY, load_fixture, make_data_response

DAY = date(2024, 5, 11)
ENTITIES = sorted(L1_SCHEMAS)


@pytest.fixture
def lake(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "lake")


def _all_entities() -> dict[str, list[dict[str, Any]]]:
    return {entity: [make_l1_row(entity)] for entity in ENTITIES}


@pytest.mark.parametrize("entity", ENTITIES)
def test_file_schema_follows_the_entity_spec(entity: str) -> None:
    schema = arrow_schema(entity)
    spec = L1_SCHEMAS[entity]
    expected_types = {
        ColumnType.INT: pa.int64(),
        ColumnType.FLOAT: pa.float64(),
        ColumnType.STR: pa.string(),
        ColumnType.BOOL: pa.bool_(),
        ColumnType.TIMESTAMP: pa.timestamp("us", tz="UTC"),
        ColumnType.DATE: pa.date32(),
        ColumnType.FLOAT_LIST: pa.list_(pa.float64()),
        ColumnType.STR_LIST: pa.list_(pa.string()),
    }

    assert schema.names == list(spec.column_names)
    for column in spec.columns:
        field = schema.field(column.name)
        assert field.nullable is column.nullable
        assert field.type == expected_types[column.type]


@pytest.mark.parametrize("entity", ENTITIES)
def test_rows_survive_a_round_trip_unchanged(lake: LocalObjectStore, entity: str) -> None:
    row = make_l1_row(entity)

    stored = write_partition(lake, entity, DAY, [row])

    assert stored.key == key_l1(entity, DAY)
    assert read_partition(lake, entity, DAY) == [row]


@pytest.mark.parametrize("entity", ENTITIES)
def test_unknown_values_come_back_as_none(lake: LocalObjectStore, entity: str) -> None:
    unknown = {c.name: None for c in L1_SCHEMAS[entity].columns if c.nullable}
    row = make_l1_row(entity, **unknown)

    write_partition(lake, entity, DAY, [row])

    assert read_partition(lake, entity, DAY) == [row]


def test_an_empty_partition_is_written_and_differs_from_a_missing_one(
    lake: LocalObjectStore,
) -> None:
    assert not partition_exists(lake, "flares", DAY)
    with pytest.raises(ObjectNotFoundError):
        read_partition(lake, "flares", DAY)

    write_partition(lake, "flares", DAY, [])

    assert partition_exists(lake, "flares", DAY)
    assert read_partition(lake, "flares", DAY) == []


def test_the_same_rows_give_the_same_bytes_and_one_object(lake: LocalObjectStore) -> None:
    rows = [make_l1_row("flares"), make_l1_row("flares", flare_class="M3.1")]

    first = write_partition(lake, "flares", DAY, rows)
    second = write_partition(lake, "flares", DAY, rows)

    assert first == second
    assert lake.list_keys() == [key_l1("flares", DAY)]
    assert first.sha256 == hashlib.sha256(lake.get(first.key)).hexdigest()


def test_rows_that_break_the_schema_are_not_written(lake: LocalObjectStore) -> None:
    bad = make_l1_row("cmes", cme_id="other", speed_km_s=0)

    with pytest.raises(PartitionError, match="ноль вместо None"):
        write_partition(lake, "cmes", DAY, [make_l1_row("cmes"), bad])

    assert lake.list_keys() == []


def test_a_file_with_another_schema_is_refused_on_read(lake: LocalObjectStore) -> None:
    sink = io.BytesIO()
    pq.write_table(pa.table({"noaa_number": [1]}), sink)
    lake.put(key_l1("active_regions", DAY), sink.getvalue())

    with pytest.raises(PartitionError, match="схема файла"):
        read_partition(lake, "active_regions", DAY)


def test_write_day_writes_all_seven_entities(lake: LocalObjectStore) -> None:
    stored = write_day(lake, DAY, _all_entities())

    assert set(stored) == set(L1_SCHEMAS)
    assert lake.list_keys("l1/") == sorted(key_l1(e, DAY) for e in L1_SCHEMAS)


def test_write_day_writes_nothing_when_one_entity_is_bad(lake: LocalObjectStore) -> None:
    rows = _all_entities()
    rows["geomag_obs"] = [make_l1_row("geomag_obs", kp_max=12.0)]

    with pytest.raises(PartitionError, match="geomag_obs"):
        write_day(lake, DAY, rows)

    assert lake.list_keys() == []


def test_write_day_needs_exactly_the_known_entities(lake: LocalObjectStore) -> None:
    rows = _all_entities()
    del rows["cmes"]

    with pytest.raises(PartitionError, match="ожидались сущности"):
        write_day(lake, DAY, rows)


def test_a_normalized_fixture_day_is_written_and_read_back(lake: LocalObjectStore) -> None:
    """Сквозная проверка: ответ источника, строки L1, Parquet и те же строки на чтении."""
    response = make_data_response(load_fixture(QUIET_DATA), at=QUIET_END_OF_DAY, no_live=True)
    part = normalize_response(response, raw_key="live/2026-09-01/2359/data_ut_day_nolive.json")

    write_day(lake, part.day, part.rows)

    for entity, rows in part.rows.items():
        assert read_partition(lake, entity, part.day) == list(rows)


def test_read_range_returns_days_in_order_and_names_the_gaps(lake: LocalObjectStore) -> None:
    for day in (date(2024, 5, 9), date(2024, 5, 11)):
        write_partition(lake, "geomag_obs", day, [make_l1_row("geomag_obs")])

    found, missing = read_range(lake, "geomag_obs", date(2024, 5, 9), date(2024, 5, 12))

    assert list(found) == [date(2024, 5, 9), date(2024, 5, 11)]
    assert missing == [date(2024, 5, 10), date(2024, 5, 12)]
    assert found[date(2024, 5, 9)] == [make_l1_row("geomag_obs")]


def test_read_range_of_a_single_day_and_of_a_reversed_period(lake: LocalObjectStore) -> None:
    write_partition(lake, "flares", DAY, [])

    assert read_range(lake, "flares", DAY, DAY) == ({DAY: []}, [])
    with pytest.raises(ValueError, match="раньше начала"):
        read_range(lake, "flares", date(2024, 5, 12), DAY)
