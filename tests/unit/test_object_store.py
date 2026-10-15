"""Локальное хранилище объектов: запись, чтение, перезапись, листинг, защита от выхода из каталога."""

import hashlib
from pathlib import Path

import pytest

from heliogeo.storage.objects import (
    LocalObjectStore,
    ObjectNotFoundError,
    ObjectStore,
    sha256_hex,
)

KEY = "warehouse/srs/2026/20260905SRS.txt"
BODY = b"Region 4210\r\n"


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "raw")


def test_local_store_satisfies_the_interface(store: LocalObjectStore) -> None:
    typed: ObjectStore = store
    assert typed.list_keys() == []


def test_put_returns_size_and_sha256_of_the_bytes(store: LocalObjectStore) -> None:
    stored = store.put(KEY, BODY)

    assert stored.key == KEY
    assert stored.size_bytes == len(BODY)
    assert stored.sha256 == hashlib.sha256(BODY).hexdigest() == sha256_hex(BODY)


def test_bytes_come_back_exactly_as_written(store: LocalObjectStore) -> None:
    """Сырьё хранится байтами как есть: перевод строки и кодировка не нормализуются."""
    store.put(KEY, BODY)

    assert store.get(KEY) == BODY


def test_put_replaces_the_object_atomically_without_leftovers(store: LocalObjectStore) -> None:
    store.put(KEY, b"first")
    store.put(KEY, b"second")

    assert store.get(KEY) == b"second"
    assert store.list_keys() == [KEY]
    leftovers = [p for p in (store._root).rglob("*") if p.is_file() and p.name.endswith(".tmp")]
    assert leftovers == []


def test_head_reports_a_missing_object_as_none(store: LocalObjectStore) -> None:
    assert store.head(KEY) is None
    stored = store.put(KEY, BODY)
    assert store.head(KEY) == stored


def test_get_of_a_missing_object_raises(store: LocalObjectStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        store.get(KEY)


def test_list_keys_filters_by_string_prefix_and_sorts(store: LocalObjectStore) -> None:
    for key in [
        "l1/flares/dt=2026-10-05/part-000.parquet",
        "l1/cmes/dt=2026-10-05/part-000.parquet",
        "_manifest/2026-10-05.json",
    ]:
        store.put(key, b"x")

    assert store.list_keys("l1/") == [
        "l1/cmes/dt=2026-10-05/part-000.parquet",
        "l1/flares/dt=2026-10-05/part-000.parquet",
    ]
    assert store.list_keys("l1/f") == ["l1/flares/dt=2026-10-05/part-000.parquet"]
    assert store.list_keys("nothing") == []


@pytest.mark.parametrize("key", ["../outside.txt", "a/../../outside.txt", "/abs.txt", "a//b", ""])
def test_keys_that_could_leave_the_root_are_rejected(
    store: LocalObjectStore, tmp_path: Path, key: str
) -> None:
    with pytest.raises(ValueError):
        store.put(key, b"x")
    with pytest.raises(ValueError):
        store.get(key)
    assert not (tmp_path / "outside.txt").exists()
