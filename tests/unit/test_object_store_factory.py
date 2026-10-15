"""Выбор хранилища по настройкам: локальный каталог или Object Storage, имена бакетов."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from heliogeo.core.config import Settings
from heliogeo.storage.factory import open_object_store
from heliogeo.storage.keys import Bucket
from heliogeo.storage.objects import LocalObjectStore
from heliogeo.storage.s3 import S3ObjectStore


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_local_store_lives_in_a_subdirectory_named_like_the_bucket(tmp_path: Path) -> None:
    settings = _settings(OBJECT_STORE="local", OBJECT_STORE_DIR=tmp_path, BUCKET_PREFIX="hg")

    store = open_object_store(Bucket.RAW, settings)
    store.put("_manifest/2026-10-05.json", b"{}")

    assert isinstance(store, LocalObjectStore)
    assert (tmp_path / "hg-raw" / "_manifest" / "2026-10-05.json").read_bytes() == b"{}"


def test_each_bucket_gets_its_own_directory(tmp_path: Path) -> None:
    settings = _settings(OBJECT_STORE_DIR=tmp_path)

    open_object_store(Bucket.RAW, settings).put("a.json", b"1")
    open_object_store(Bucket.LAKE, settings).put("a.json", b"2")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["heliogeo-lake", "heliogeo-raw"]


def test_s3_is_selected_by_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    store = open_object_store(Bucket.LAKE, _settings(OBJECT_STORE="s3", BUCKET_PREFIX="hg"))

    assert isinstance(store, S3ObjectStore)
    assert store._bucket == "hg-lake"


def test_defaults_point_at_a_local_store_and_the_yandex_endpoint() -> None:
    settings = _settings()

    assert settings.object_store == "local"
    assert settings.bucket_prefix == "heliogeo"
    assert settings.s3_endpoint_url == "https://storage.yandexcloud.net"
    assert settings.s3_region == "ru-central1"


def test_a_relative_store_dir_is_resolved_from_the_repository_root() -> None:
    settings = _settings(OBJECT_STORE_DIR="somewhere/objects")

    assert settings.object_store_dir.is_absolute()
    assert settings.object_store_dir.parts[-2:] == ("somewhere", "objects")


def test_an_unknown_store_kind_is_a_configuration_error() -> None:
    with pytest.raises(ValidationError):
        _settings(OBJECT_STORE="ftp")


def test_a_bad_bucket_prefix_is_refused(tmp_path: Path) -> None:
    settings = _settings(OBJECT_STORE_DIR=tmp_path, BUCKET_PREFIX="Bad_Prefix")

    with pytest.raises(ValueError):
        open_object_store(Bucket.RAW, settings)
