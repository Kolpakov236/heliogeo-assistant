"""Хранилище объектов в Yandex Object Storage (S3-совместимый API).

Реализует тот же интерфейс ``ObjectStore``, что и локальный каталог, поэтому код функций не знает,
где лежат данные. Клиент передаётся снаружи: в тестах это клиент с заглушкой ответов, в облаке —
клиент из ``make_s3_client``.

Два свойства облака, на которые опирается остальной код:

* SHA-256 содержимого записывается в пользовательские метаданные объекта (``sha256``): ETag S3 —
  не SHA-256, а сверять содержимое, не скачивая его, нужно (``RawStore`` сравнивает хеши). Для
  объекта без метаданных (например, залитого синхронизацией архива) хеш считается по содержимому.
* Запись объекта целиком и атомарна: читатель видит либо старое, либо новое содержимое.

Повторы при временных сбоях делает сам клиент (``retries`` в ``make_s3_client``). Остальные ошибки
(нет прав, нет бакета) не скрываются: это ``botocore.exceptions.ClientError``, а перевод в класс
ошибки шага — дело обработчика функции.
"""

from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from heliogeo.storage.keys import validate_key
from heliogeo.storage.objects import ObjectNotFoundError, StoredObject, sha256_hex

_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})
_SHA_METADATA = "sha256"


def make_s3_client(*, endpoint_url: str, region: str) -> BaseClient:
    """Клиент S3 для Object Storage.

    Ключи доступа берутся из стандартной цепочки boto3 (переменные ``AWS_ACCESS_KEY_ID`` и
    ``AWS_SECRET_ACCESS_KEY``). В облаке это статический ключ сервисного аккаунта функции,
    хранящийся в Lockbox; в настройки приложения и в репозиторий ключи не попадают.
    """
    config = Config(
        retries={"total_max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=30,
    )
    client: BaseClient = boto3.client(
        "s3", endpoint_url=endpoint_url, region_name=region, config=config
    )
    return client


def _is_not_found(error: ClientError) -> bool:
    return str(error.response.get("Error", {}).get("Code")) in _NOT_FOUND


class S3ObjectStore:
    """Один бакет Object Storage."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        self._client: Any = client
        self._bucket = bucket

    def put(self, key: str, data: bytes) -> StoredObject:
        digest = sha256_hex(data)
        self._client.put_object(
            Bucket=self._bucket, Key=validate_key(key), Body=data, Metadata={_SHA_METADATA: digest}
        )
        return StoredObject(key=key, size_bytes=len(data), sha256=digest)

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=validate_key(key))
        except ClientError as error:
            if _is_not_found(error):
                raise ObjectNotFoundError(key) from None
            raise
        body: bytes = response["Body"].read()
        return body

    def head(self, key: str) -> StoredObject | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=validate_key(key))
        except ClientError as error:
            if _is_not_found(error):
                return None
            raise
        digest = (response.get("Metadata") or {}).get(_SHA_METADATA)
        if digest is None:
            digest = sha256_hex(self.get(key))
        return StoredObject(key=key, size_bytes=int(response["ContentLength"]), sha256=digest)

    def list_keys(self, prefix: str = "") -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return sorted(keys)
