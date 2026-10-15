"""Запись сырья: тела ответов источника сохраняются байтами как получены, с манифестом за дату.

Содержимое объекта не пересериализуется: хеш считается от тех самых байтов, которые пришли от
источника. Повторная запись тех же байтов ничего не меняет (идемпотентность). Если под тем же
ключом пришло другое содержимое, объект заменяется и это попадает в предупреждение и в манифест:
бакет версионирован, старая версия остаётся в истории, а вот молчаливо потерять расхождение
нельзя.

Модуль не читает часы и сеть: время получения передаёт вызывающий (оно есть в ответе клиента).
Проверка, что тело — настоящий ответ, а не страница ошибки, лежит на вызывающем: сохранять нужно
только разобранные успешные ответы.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from heliogeo.core.logging import get_logger
from heliogeo.storage.keys import key_manifest, key_raw_snapshot
from heliogeo.storage.objects import ObjectNotFoundError, ObjectStore, sha256_hex

logger = get_logger(__name__)

_MANIFEST_VERSION = 1


class ManifestError(Exception):
    """Манифест за дату прочитан, но не похож на манифест: перезаписывать его молча нельзя."""


class SaveStatus(StrEnum):
    """Что произошло при записи тела."""

    STORED = "stored"
    UNCHANGED = "unchanged"
    REPLACED = "replaced"


@dataclass(frozen=True, kw_only=True)
class RawEntry:
    """Запись манифеста о сохранённом теле ответа.

    Attributes:
        key: ключ объекта в бакете ``raw``.
        endpoint: ``data`` или ``text``.
        requested_at: запрошенный момент (UTC).
        window: окно запроса; ``None`` — по умолчанию (сутки UTC); у ``text`` всегда ``None``.
        no_live: запрещено ли было подмешивание live-данных. При ``False`` ответ за свежую дату
            мог содержать live-данные и не воспроизводится.
        size_bytes: размер тела.
        sha256: SHA-256 тела.
        fetched_at: когда тело получено впервые.
        replaced_sha256: хеши тел, которые были заменены под этим ключом, по порядку замен.
    """

    key: str
    endpoint: Literal["data", "text"]
    requested_at: datetime
    window: str | None
    no_live: bool
    size_bytes: int
    sha256: str
    fetched_at: datetime
    replaced_sha256: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "endpoint": self.endpoint,
            "requested_at": _iso(self.requested_at),
            "window": self.window,
            "no_live": self.no_live,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "fetched_at": _iso(self.fetched_at),
            "replaced_sha256": list(self.replaced_sha256),
        }

    @classmethod
    def from_json(cls, row: Any) -> Self:
        try:
            return cls(
                key=row["key"],
                endpoint=row["endpoint"],
                requested_at=datetime.fromisoformat(row["requested_at"]),
                window=row["window"],
                no_live=bool(row["no_live"]),
                size_bytes=int(row["size_bytes"]),
                sha256=row["sha256"],
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
                replaced_sha256=tuple(row["replaced_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ManifestError(f"запись манифеста не разобрана: {exc!r}") from exc


@dataclass(frozen=True, kw_only=True)
class RawRecord:
    """Результат записи: запись манифеста и что произошло.

    Attributes:
        entry: запись манифеста об объекте.
        status: ``stored`` (новый), ``unchanged`` (те же байты) или ``replaced`` (другие байты).
    """

    entry: RawEntry
    status: SaveStatus


@dataclass(frozen=True, kw_only=True)
class Manifest:
    """Манифест за дату: что сохранено в сыром слое.

    Attributes:
        day: дата (UTC) запрошенных моментов.
        entries: записи по ключам, по алфавиту ключей.
    """

    day: date
    entries: tuple[RawEntry, ...] = field(default_factory=tuple)

    def to_bytes(self) -> bytes:
        """Детерминированный JSON: одинаковое содержимое даёт одинаковые байты."""
        body = {
            "version": _MANIFEST_VERSION,
            "date": self.day.isoformat(),
            "entries": [entry.to_json() for entry in self.entries],
        }
        return (json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()

    @classmethod
    def from_bytes(cls, data: bytes, *, day: date) -> Self:
        try:
            body = json.loads(data)
            if body["version"] != _MANIFEST_VERSION or body["date"] != day.isoformat():
                raise ManifestError(
                    f"манифест версии {body['version']!r} за {body['date']!r}, "
                    f"ожидался версии {_MANIFEST_VERSION} за {day.isoformat()}"
                )
            entries = tuple(RawEntry.from_json(row) for row in body["entries"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ManifestError(f"манифест за {day.isoformat()} не разобран: {exc!r}") from exc
        return cls(day=day, entries=entries)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RawStore:
    """Сырой слой поверх хранилища объектов (в облаке — бакет ``raw``)."""

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def load_manifest(self, day: date) -> Manifest:
        """Манифест за дату; если его ещё нет — пустой.

        Raises:
            ManifestError: если манифест есть, но не разбирается.
        """
        try:
            data = self._store.get(key_manifest(day))
        except ObjectNotFoundError:
            return Manifest(day=day)
        return Manifest.from_bytes(data, day=day)

    def save_snapshot(
        self,
        *,
        endpoint: Literal["data", "text"],
        body: bytes,
        requested_at: datetime,
        window: str | None,
        no_live: bool,
        fetched_at: datetime,
    ) -> RawRecord:
        """Сохранить тело ответа ``/data`` или ``/text`` и отразить его в манифесте за дату.

        Args:
            endpoint: какой эндпоинт ответил.
            body: тело ответа байтами, как получено.
            requested_at: запрошенный момент (с часовым поясом): по нему выбираются дата и ключ.
            window: запрошенное окно; ``None`` — по умолчанию. Для ``text`` не используется.
            no_live: был ли запрещён live.
            fetched_at: когда тело получено (с часовым поясом).

        Raises:
            ValueError: если момент без часового пояса или тело пустое.
            ManifestError: если манифест за дату испорчен.
        """
        if not body:
            raise ValueError("пустое тело ответа сохранять нельзя")
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError(f"fetched_at должен быть с часовым поясом, получено {fetched_at!r}")
        window = None if endpoint == "text" else window
        key = key_raw_snapshot(endpoint, requested_at, window, no_live)
        moment = requested_at.astimezone(UTC)
        manifest = self.load_manifest(moment.date())
        previous = {entry.key: entry for entry in manifest.entries}.get(key)

        digest = sha256_hex(body)
        if previous is not None and previous.sha256 == digest:
            stored = self._store.head(key)
            if stored is not None and stored.sha256 == digest:
                return RawRecord(entry=previous, status=SaveStatus.UNCHANGED)
            logger.warning("Raw object %s is missing or damaged, writing it again", key)
            self._store.put(key, body)
            return RawRecord(entry=previous, status=SaveStatus.STORED)

        self._store.put(key, body)
        if previous is None:
            status, replaced = SaveStatus.STORED, tuple[str, ...]()
        else:
            status, replaced = SaveStatus.REPLACED, (*previous.replaced_sha256, previous.sha256)
            logger.warning(
                "Raw object %s changed: sha256 %s -> %s (old version stays in bucket history)",
                key,
                previous.sha256[:12],
                digest[:12],
            )
        entry = RawEntry(
            key=key,
            endpoint=endpoint,
            requested_at=moment,
            window=window,
            no_live=no_live,
            size_bytes=len(body),
            sha256=digest,
            fetched_at=fetched_at.astimezone(UTC),
            replaced_sha256=replaced,
        )
        others = [e for e in manifest.entries if e.key != key]
        updated = Manifest(day=manifest.day, entries=tuple(sorted([*others, entry], key=_by_key)))
        self._store.put(key_manifest(manifest.day), updated.to_bytes())
        return RawRecord(entry=entry, status=status)


def _by_key(entry: RawEntry) -> str:
    return entry.key
