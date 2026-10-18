"""Хранилище объектов: интерфейс и локальная реализация на файловой системе.

Интерфейс ``ObjectStore`` — это шов между кодом функций и Object Storage. Код работает только с
ним, поэтому «локально ↔ облако» переключается значением окружения, а не правкой кода. Реализация
для Yandex Object Storage (S3-совместимый API) подключается к тому же интерфейсу; при её
написании нужно учесть два свойства облака, которые локальная реализация повторяет:

* запись объекта целиком и атомарна: читатель видит либо старое, либо новое содержимое;
* контрольная сумма SHA-256 хранится вместе с объектом (в облаке — в пользовательских метаданных,
  потому что ETag S3 не равен SHA-256), чтобы сверять содержимое, не скачивая его.

Ключи строятся функциями ``heliogeo.storage.keys``. Здесь они только проверяются.
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from heliogeo.storage.keys import validate_key


class ObjectNotFoundError(Exception):
    """Объекта с таким ключом нет."""


@dataclass(frozen=True, kw_only=True)
class StoredObject:
    """Что известно об объекте, не читая его.

    Attributes:
        key: ключ объекта в бакете.
        size_bytes: размер в байтах.
        sha256: SHA-256 содержимого в шестнадцатеричном виде.
    """

    key: str
    size_bytes: int
    sha256: str


class ObjectStore(Protocol):
    """Один бакет: запись и чтение объектов целиком по ключу."""

    def put(self, key: str, data: bytes) -> StoredObject:
        """Записать объект, заменив существующий с тем же ключом."""
        ...

    def get(self, key: str) -> bytes:
        """Прочитать объект.

        Raises:
            ObjectNotFoundError: если объекта нет.
        """
        ...

    def head(self, key: str) -> StoredObject | None:
        """Сведения об объекте или ``None``, если его нет."""
        ...

    def list_keys(self, prefix: str = "") -> list[str]:
        """Ключи с данным началом строки (как в S3, не обязательно по границе каталога), по алфавиту."""
        ...


def sha256_hex(data: bytes) -> str:
    """SHA-256 содержимого в шестнадцатеричном виде."""
    return hashlib.sha256(data).hexdigest()


class LocalObjectStore:
    """Объекты в каталоге: ключ ``a/b/c.json`` — файл ``<root>/a/b/c.json``.

    Для разработки и тестов без облака. Запись атомарна: данные пишутся во временный файл рядом
    и подменяют целевой одной операцией. История версий не хранится (в облаке её ведёт
    версионирование бакета).
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*validate_key(key).split("/"))

    def put(self, key: str, data: bytes) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Временное имя начинается с точки: ключ так начаться не может, и list_keys его пропускает.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return StoredObject(key=key, size_bytes=len(data), sha256=sha256_hex(data))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError(key) from None

    def head(self, key: str) -> StoredObject | None:
        try:
            data = self.get(key)
        except ObjectNotFoundError:
            return None
        return StoredObject(key=key, size_bytes=len(data), sha256=sha256_hex(data))

    def list_keys(self, prefix: str = "") -> list[str]:
        if not self._root.is_dir():
            return []
        keys = (
            path.relative_to(self._root).as_posix()
            for path in self._root.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )
        return sorted(key for key in keys if key.startswith(prefix))
