"""Хранилище Object Storage: поведение на заглушке ответов S3, без сети и без реальных ключей."""

import hashlib
import io
from collections.abc import Iterator

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from heliogeo.storage.objects import ObjectNotFoundError, ObjectStore, sha256_hex
from heliogeo.storage.s3 import S3ObjectStore, make_s3_client

BUCKET = "heliogeo-raw"
KEY = "warehouse/srs/2026/20260905SRS.txt"
BODY = b"Region 4210\r\n"


@pytest.fixture
def client() -> BaseClient:
    return boto3.client(
        "s3",
        region_name="ru-central1",
        endpoint_url="https://storage.yandexcloud.net",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture
def stubber(client: BaseClient) -> Iterator[Stubber]:
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()


@pytest.fixture
def store(client: BaseClient, stubber: Stubber) -> S3ObjectStore:
    return S3ObjectStore(client, BUCKET)


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


def test_the_store_satisfies_the_interface(store: S3ObjectStore) -> None:
    typed: ObjectStore = store
    assert typed is store


def test_put_writes_the_bytes_with_their_sha256_in_metadata(
    store: S3ObjectStore, stubber: Stubber
) -> None:
    digest = hashlib.sha256(BODY).hexdigest()
    stubber.add_response(
        "put_object",
        {},
        expected_params={
            "Bucket": BUCKET,
            "Key": KEY,
            "Body": BODY,
            "Metadata": {"sha256": digest},
        },
    )

    stored = store.put(KEY, BODY)

    assert (stored.key, stored.size_bytes, stored.sha256) == (KEY, len(BODY), digest)


def test_get_returns_the_bytes(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_response(
        "get_object", {"Body": _body(BODY)}, expected_params={"Bucket": BUCKET, "Key": KEY}
    )

    assert store.get(KEY) == BODY


def test_get_of_a_missing_object_raises_not_found(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)

    with pytest.raises(ObjectNotFoundError):
        store.get(KEY)


def test_other_errors_are_not_hidden(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)

    with pytest.raises(ClientError):
        store.get(KEY)


def test_head_uses_the_sha256_from_metadata(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_response(
        "head_object",
        {"ContentLength": len(BODY), "Metadata": {"sha256": "abc"}},
        expected_params={"Bucket": BUCKET, "Key": KEY},
    )

    stored = store.head(KEY)

    assert stored is not None
    assert (stored.size_bytes, stored.sha256) == (len(BODY), "abc")


def test_head_computes_the_hash_for_an_object_without_metadata(
    store: S3ObjectStore, stubber: Stubber
) -> None:
    """Объект залит синхронизацией архива, без метаданных: хеш считается по содержимому."""
    stubber.add_response("head_object", {"ContentLength": len(BODY)})
    stubber.add_response("get_object", {"Body": _body(BODY)})

    stored = store.head(KEY)

    assert stored is not None
    assert stored.sha256 == sha256_hex(BODY)


def test_head_of_a_missing_object_is_none(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_client_error("head_object", service_error_code="404", http_status_code=404)

    assert store.head(KEY) is None


def test_list_keys_follows_pages_and_sorts(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "l1/flares/b"}], "IsTruncated": True, "NextContinuationToken": "t"},
        expected_params={"Bucket": BUCKET, "Prefix": "l1/"},
    )
    stubber.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "l1/cmes/a"}], "IsTruncated": False},
        expected_params={"Bucket": BUCKET, "Prefix": "l1/", "ContinuationToken": "t"},
    )

    assert store.list_keys("l1/") == ["l1/cmes/a", "l1/flares/b"]


def test_list_keys_of_an_empty_prefix_is_empty(store: S3ObjectStore, stubber: Stubber) -> None:
    stubber.add_response("list_objects_v2", {"IsTruncated": False})

    assert store.list_keys("nothing") == []


@pytest.mark.parametrize("key", ["../a", "a//b", "", "/a"])
def test_a_bad_key_is_refused_before_any_request(store: S3ObjectStore, key: str) -> None:
    with pytest.raises(ValueError):
        store.put(key, b"x")
    with pytest.raises(ValueError):
        store.get(key)
    with pytest.raises(ValueError):
        store.head(key)


def test_the_client_is_made_for_the_given_endpoint_with_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    client = make_s3_client(endpoint_url="https://storage.yandexcloud.net", region="ru-central1")

    assert client.meta.endpoint_url == "https://storage.yandexcloud.net"
    assert client.meta.region_name == "ru-central1"
    assert client.meta.config.retries["total_max_attempts"] == 3
