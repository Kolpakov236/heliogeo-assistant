"""Выбор хранилища объектов по настройкам: локальный каталог или Object Storage.

Различие «локально ↔ облако» — только значения переменных окружения (``OBJECT_STORE`` и др.), в коде
функций его нет. Имя бакета везде одно: ``{BUCKET_PREFIX}-{raw|lake|artifacts|issues}``; локально оно
становится подкаталогом ``OBJECT_STORE_DIR``, поэтому раскладка на диске повторяет раскладку в облаке.
"""

from heliogeo.core.config import Settings, get_settings
from heliogeo.storage.keys import Bucket, bucket_name
from heliogeo.storage.objects import LocalObjectStore, ObjectStore
from heliogeo.storage.s3 import S3ObjectStore, make_s3_client


def open_object_store(bucket: Bucket, settings: Settings | None = None) -> ObjectStore:
    """Хранилище бакета ``bucket`` по настройкам.

    Raises:
        ValueError: если префикс бакета не годится для имени бакета.
    """
    settings = settings or get_settings()
    name = bucket_name(settings.bucket_prefix, bucket)
    if settings.object_store == "s3":
        client = make_s3_client(endpoint_url=settings.s3_endpoint_url, region=settings.s3_region)
        return S3ObjectStore(client, name)
    return LocalObjectStore(settings.object_store_dir / name)
