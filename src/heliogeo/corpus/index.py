"""Индекс корпуса текстов и быстрый доступ к нему.

Сырой слой хранит ответы ``/text`` как получены: тысячи небольших файлов по датам. Для работы с
ними (подбор примеров, частоты оборотов, пары «данные → текст») их нужно было бы каждый раз читать
и разбирать заново. Индекс собирается один раз: один объект ``corpus/texts_v1.jsonl`` в бакете
``lake``, по строке на выпуск, по возрастанию времени выпуска. ``Corpus.load`` читает его целиком за
доли секунды, дальше всё идёт в памяти.

Сборка идемпотентна и детерминирована: те же тела в сыром слое дают те же байты индекса. Один и тот
же выпуск, сохранённый под несколькими ключами (запрос на 00:30, когда источник ответил выпуском
накануне вечером), входит в индекс один раз; предпочитается тело, запрошенное ровно на время выпуска.

Расписание источника на сегодня: у суток три выпуска — Discussion 00:30 и 12:30, RSGA 22:00
(``EXPECTED_ISSUES``). По нему ``missing_issues`` находит дыры в архиве.
"""

import json
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Self

from heliogeo.core.logging import get_logger
from heliogeo.corpus.records import CorpusError, TextRecord, record_from_response, split_sentences
from heliogeo.extractors.swx.response import SwxResponseError, SwxTextResponse
from heliogeo.mirror.raw import ManifestError, RawEntry, RawStore
from heliogeo.storage.keys import key_corpus
from heliogeo.storage.objects import ObjectNotFoundError, ObjectStore, sha256_hex

logger = get_logger(__name__)

INDEX_NAME = "texts_v1.jsonl"
EXPECTED_ISSUES: tuple[tuple[str, str], ...] = (
    ("discussion", "0030"),
    ("discussion", "1230"),
    ("rsga", "2200"),
)

_MANIFEST_PREFIX = "_manifest/"
_MANIFEST_KEY = re.compile(r"_manifest/(\d{4}-\d{2}-\d{2})\.json")


@dataclass
class BuildReport:
    """Итог сборки индекса.

    Attributes:
        days_scanned: сколько манифестов прочитано.
        records: сколько выпусков вошло в индекс.
        by_kind: выпусков по видам.
        duplicates: сколько тел оказалось копией уже учтённого выпуска.
        skipped: что пропущено и почему: ``(ключ, причина)``.
    """

    days_scanned: int = 0
    records: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


class Corpus:
    """Выпуски текста NOAA в памяти: выбор, поиск по разделам, предложения, пропуски в архиве."""

    def __init__(self, records: Sequence[TextRecord]) -> None:
        self._records = tuple(sorted(records, key=lambda r: r.issued_utc))
        self._by_issue = {r.issued_utc: r for r in self._records}

    # --- чтение и запись индекса ---

    @classmethod
    def load(cls, lake_store: ObjectStore) -> Self:
        """Прочитать индекс из бакета ``lake``.

        Raises:
            CorpusError: если индекса нет или он испорчен (индекс собирается ``build_corpus``).
        """
        try:
            data = lake_store.get(key_corpus(INDEX_NAME))
        except ObjectNotFoundError:
            raise CorpusError(
                "индекса корпуса нет: соберите его командой `heliogeo corpus build`"
            ) from None
        return cls.from_bytes(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        records: list[TextRecord] = []
        for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpusError(f"строка {number} индекса не JSON: {exc}") from exc
            records.append(TextRecord.from_json(row))
        return cls(records)

    def to_bytes(self) -> bytes:
        """Детерминированный JSONL: одинаковое содержимое даёт одинаковые байты."""
        lines = (
            json.dumps(r.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for r in self._records
        )
        return ("\n".join(lines) + "\n").encode("utf-8") if self._records else b""

    # --- доступ ---

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[TextRecord]:
        return iter(self._records)

    @property
    def records(self) -> tuple[TextRecord, ...]:
        return self._records

    def get(self, issued_utc: datetime) -> TextRecord | None:
        """Выпуск по точному времени выпуска (UTC, с часовым поясом)."""
        return self._by_issue.get(issued_utc)

    def select(
        self,
        *,
        kind: str | None = None,
        cycle: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[TextRecord]:
        """Выпуски по виду, циклу и датам выпуска (границы включены)."""
        return [
            r
            for r in self._records
            if (kind is None or r.kind == kind)
            and (cycle is None or r.cycle == cycle)
            and (start is None or r.day >= start)
            and (end is None or r.day <= end)
        ]

    def sections(
        self, name: str, *, part: str = "text", kind: str | None = None
    ) -> Iterator[tuple[TextRecord, str]]:
        """Тексты раздела ``name`` по выпускам: ``(выпуск, текст)``.

        Для Discussion части раздела — ``text``, ``24 hr Summary``, ``Forecast``; для RSGA
        разделы — ``solar_analysis``, ``solar_forecast``, ``geophysical_summary`` и другие.
        """
        for record in self._records:
            if kind is not None and record.kind != kind:
                continue
            text = record.section_text(name, part)
            if text:
                yield record, text

    def search(
        self, pattern: str, *, kind: str | None = None, ignore_case: bool = True
    ) -> Iterator[tuple[TextRecord, str]]:
        """Строки текста, подходящие под регулярное выражение: ``(выпуск, строка)``."""
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        for record in self._records:
            if kind is not None and record.kind != kind:
                continue
            for line in record.text.splitlines():
                if regex.search(line):
                    yield record, line.strip()

    def sentences(
        self, record: TextRecord, section: str | None = None, part: str = "text"
    ) -> list[str]:
        """Предложения выпуска целиком или одного раздела."""
        if section is None:
            return split_sentences(record.text)
        return split_sentences(record.section_text(section, part) or "")

    # --- пропуски и сводка ---

    def missing_issues(
        self, start: date | None = None, end: date | None = None
    ) -> list[tuple[date, str]]:
        """Ожидаемые по расписанию, но отсутствующие выпуски: ``(сутки, «вид цикл»)``.

        По умолчанию — от первых до последних суток корпуса.
        """
        if not self._records:
            return []
        first = start or self._records[0].day
        last = end or self._records[-1].day
        present = {(r.day, r.kind, r.cycle) for r in self._records}
        missing: list[tuple[date, str]] = []
        for ordinal in range(first.toordinal(), last.toordinal() + 1):
            day = date.fromordinal(ordinal)
            missing.extend(
                (day, f"{kind} {cycle}")
                for kind, cycle in EXPECTED_ISSUES
                if (day, kind, cycle) not in present
            )
        return missing

    def stats(self) -> dict[str, object]:
        """Сводка: сколько выпусков, какие виды, какой период, сколько ожидаемых выпусков нет."""
        if not self._records:
            return {"records": 0}
        return {
            "records": len(self._records),
            "by_kind": dict(sorted(Counter(r.kind for r in self._records).items())),
            "by_cycle": dict(sorted(Counter(f"{r.kind} {r.cycle}" for r in self._records).items())),
            "first_issued": self._records[0].issued_utc.isoformat(),
            "last_issued": self._records[-1].issued_utc.isoformat(),
            "days": len({r.day for r in self._records}),
            "missing_expected": len(self.missing_issues()),
            "records_with_problems": sum(1 for r in self._records if r.problems),
        }


# --- сборка ---


def _manifest_days(raw_store: ObjectStore, report: BuildReport) -> list[date]:
    days: list[date] = []
    for key in raw_store.list_keys(_MANIFEST_PREFIX):
        match = _MANIFEST_KEY.fullmatch(key)
        if match is None:
            report.skipped.append((key, "имя манифеста не похоже на _manifest/YYYY-MM-DD.json"))
            continue
        days.append(date.fromisoformat(match.group(1)))
    return days


def _load_record(raw_store: ObjectStore, entry: RawEntry, report: BuildReport) -> TextRecord | None:
    try:
        body = raw_store.get(entry.key)
    except ObjectNotFoundError:
        report.skipped.append((entry.key, "тела нет в хранилище, а манифест о нём знает"))
        return None
    if sha256_hex(body) != entry.sha256:
        report.skipped.append((entry.key, "хэш тела не совпал с манифестом"))
        return None
    try:
        raw = json.loads(body)
        response = SwxTextResponse.from_raw(
            raw,
            requested_at=entry.requested_at,
            fetched_at=entry.fetched_at,
            no_live=entry.no_live,
        )
        return record_from_response(response, raw_key=entry.key, raw_sha256=entry.sha256)
    except (ValueError, SwxResponseError, CorpusError) as exc:
        report.skipped.append((entry.key, f"не разобрано: {exc}"))
        return None


def build_corpus(raw_store: ObjectStore, lake_store: ObjectStore) -> tuple[Corpus, BuildReport]:
    """Собрать индекс корпуса из сырого слоя и записать его в бакет ``lake``.

    Читаются все манифесты ``raw``; из записей ``text`` берутся тела, проверяются по хэшу
    манифеста и разбираются текущим парсером (``SwxTextResponse``). Выпуск, не прошедший проверку,
    попадает в ``skipped`` отчёта, а не обрывает сборку.

    Returns:
        Корпус и отчёт о сборке.
    """
    raw = RawStore(raw_store)
    report = BuildReport()
    # время выпуска -> (приоритет, ключ тела, запись); меньше приоритет и ключ — лучше
    chosen: dict[datetime, tuple[int, str, TextRecord]] = {}

    for day in _manifest_days(raw_store, report):
        try:
            manifest = raw.load_manifest(day)
        except ManifestError as exc:
            report.skipped.append((f"{_MANIFEST_PREFIX}{day.isoformat()}.json", str(exc)))
            continue
        report.days_scanned += 1
        for entry in manifest.entries:
            if entry.endpoint != "text":
                continue
            record = _load_record(raw_store, entry, report)
            if record is None:
                continue
            rank = (0 if entry.requested_at == record.issued_utc else 1, record.raw_key)
            current = chosen.get(record.issued_utc)
            if current is None:
                chosen[record.issued_utc] = (rank[0], rank[1], record)
                continue
            report.duplicates += 1
            if rank < (current[0], current[1]):
                chosen[record.issued_utc] = (rank[0], rank[1], record)

    corpus = Corpus([item[2] for item in chosen.values()])
    lake_store.put(key_corpus(INDEX_NAME), corpus.to_bytes())
    report.records = len(corpus)
    report.by_kind = dict(sorted(Counter(r.kind for r in corpus).items()))
    logger.info("Corpus built: %d records from %d days", report.records, report.days_scanned)
    return corpus, report
