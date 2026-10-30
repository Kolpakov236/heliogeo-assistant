"""Корпус текстов: разбор выпусков, сборка индекса из сырого слоя, быстрый доступ."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from heliogeo.corpus import (
    INDEX_NAME,
    Corpus,
    CorpusError,
    TextRecord,
    build_corpus,
    split_sentences,
)
from heliogeo.corpus.records import normalize_sections, record_from_response
from heliogeo.extractors.swx.response import SwxTextResponse
from heliogeo.mirror.raw import RawStore
from heliogeo.storage.keys import key_corpus
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import FETCHED_AT, fixture_bytes, load_fixture

DISCUSSION = "swx_text_discussion_2026-01-15_0030.json"
RSGA = "swx_text_rsga_2026-01-15.json"

DISCUSSION_AT = datetime(2026, 1, 15, 0, 30, tzinfo=UTC)
RSGA_REQUEST = datetime(2026, 1, 15, 23, 59, tzinfo=UTC)  # ingest спрашивает на конец суток
RSGA_ISSUED = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)

Stores = tuple[LocalObjectStore, LocalObjectStore]


def _record(name: str, requested_at: datetime) -> TextRecord:
    response = SwxTextResponse.from_raw(
        load_fixture(name), requested_at=requested_at, fetched_at=FETCHED_AT, no_live=True
    )
    return record_from_response(response, raw_key=f"live/{name}", raw_sha256="0" * 64)


# --- Запись корпуса ---


def test_discussion_record_keeps_sections_and_their_parts() -> None:
    record = _record(DISCUSSION, DISCUSSION_AT)

    assert (record.kind, record.cycle, record.issued_utc) == ("discussion", "0030", DISCUSSION_AT)
    assert list(record.sections) == [
        "Solar Activity",
        "Energetic Particle",
        "Solar Wind",
        "Geospace",
    ]
    assert set(record.sections["Solar Activity"]) == {"text", "24 hr Summary", "Forecast"}
    assert record.section_text("Geospace", "Forecast")
    assert record.problems == ()


def test_rsga_sections_are_read_from_the_raw_body() -> None:
    """Типизированный разбор отбрасывает разделы RSGA (строки); в записи они сохраняются."""
    response = SwxTextResponse.from_raw(
        load_fixture(RSGA), requested_at=RSGA_REQUEST, fetched_at=FETCHED_AT, no_live=True
    )
    assert dict(response.sections) == {}  # ограничение текущего парсера, ради него и корпус

    record = record_from_response(response, raw_key="k", raw_sha256="0" * 64)

    assert (record.kind, record.cycle, record.issued_utc) == ("rsga", "2200", RSGA_ISSUED)
    assert len(record.sections) == 8
    assert set(record.sections["f107"]) == {"text"}
    assert "Penticton 10.7 cm Flux" in (record.section_text("f107") or "")
    assert record.probabilities == {
        "Class M": "55/55/55",
        "Class X": "10/10/10",
        "Proton": "01/01/01",
        "PCAF": "green",
    }
    assert record.problems == ()  # замечания парсера про «не объект» к записи не относятся


def test_record_covers_the_24_hours_before_issue_and_names_the_data_days() -> None:
    record = _record(DISCUSSION, DISCUSSION_AT)

    assert record.covers_to == DISCUSSION_AT
    assert record.covers_from == datetime(2026, 1, 14, 0, 30, tzinfo=UTC)
    # Discussion 00:30 за 15-е говорит о сутках 14-го: нужны данные за 14 и 15 января
    assert record.data_days == (date(2026, 1, 14), date(2026, 1, 15))
    assert record.day == date(2026, 1, 15)


def test_record_round_trips_through_json() -> None:
    record = _record(RSGA, RSGA_REQUEST)
    restored = TextRecord.from_json(json.loads(json.dumps(record.to_json())))
    assert restored == record


@pytest.mark.parametrize(
    "row",
    [{}, {"issued_utc": "вчера"}, "строка", {"issued_utc": "2026-01-15T00:30:00+00:00"}],
    ids=["empty", "bad-time", "not-a-mapping", "incomplete"],
)
def test_bad_index_row_is_a_corpus_error(row: Any) -> None:
    with pytest.raises(CorpusError, match="не разобрана"):
        TextRecord.from_json(row)


def test_response_without_issue_time_is_rejected() -> None:
    raw = load_fixture(DISCUSSION)
    del raw["issued_dt"]
    del raw["issued"]
    response = SwxTextResponse.from_raw(
        raw, requested_at=DISCUSSION_AT, fetched_at=FETCHED_AT, no_live=True
    )
    with pytest.raises(CorpusError, match="не опознан"):
        record_from_response(response, raw_key="k", raw_sha256="0" * 64)


def test_normalize_sections_accepts_objects_and_strings_and_reports_the_rest() -> None:
    sections, problems = normalize_sections({"a": {"x": "1"}, "b": "текст", "c": 5, "d": None})
    assert sections == {"a": {"x": "1"}, "b": {"text": "текст"}}
    assert len(problems) == 2
    assert normalize_sections("не объект") == ({}, ["sections не объект: str"])


def test_split_sentences_keeps_numbers_and_times_together() -> None:
    text = (
        "Solar activity was at low levels. Region 4520 (S12W07, Dai/beta) produced a C2.6/SF\n"
        "flare at 31/1516 UTC, which was the largest event. No Earth-directed CMEs were observed."
    )
    sentences = split_sentences(text)
    assert len(sentences) == 3
    assert sentences[1].endswith("largest event.")
    assert "C2.6/SF flare at 31/1516 UTC" in sentences[1]
    assert split_sentences("") == []


# --- Сборка индекса ---


@pytest.fixture
def stores(tmp_path: Path) -> Stores:
    return LocalObjectStore(tmp_path / "raw"), LocalObjectStore(tmp_path / "lake")


def _save(raw_store: LocalObjectStore, body: bytes, requested_at: datetime) -> None:
    RawStore(raw_store).save_snapshot(
        endpoint="text",
        body=body,
        requested_at=requested_at,
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )


@pytest.fixture
def filled(stores: Stores) -> Stores:
    raw_store, _ = stores
    _save(raw_store, fixture_bytes(DISCUSSION), DISCUSSION_AT)
    _save(raw_store, fixture_bytes(RSGA), RSGA_REQUEST)
    return stores


def test_build_writes_a_sorted_index_with_both_kinds(filled: Stores) -> None:
    raw_store, lake_store = filled
    corpus, report = build_corpus(raw_store, lake_store)

    assert [r.kind for r in corpus] == ["discussion", "rsga"]
    assert (report.records, report.days_scanned, report.duplicates) == (2, 1, 0)
    assert report.by_kind == {"discussion": 1, "rsga": 1}
    assert report.skipped == []
    assert lake_store.head(key_corpus(INDEX_NAME)) is not None


def test_index_is_deterministic_and_loadable(filled: Stores) -> None:
    raw_store, lake_store = filled
    build_corpus(raw_store, lake_store)
    first = lake_store.get(key_corpus(INDEX_NAME))
    build_corpus(raw_store, lake_store)

    assert lake_store.get(key_corpus(INDEX_NAME)) == first
    loaded = Corpus.load(lake_store)
    assert loaded.records == build_corpus(raw_store, lake_store)[0].records
    assert loaded.to_bytes() == first


def test_same_issue_saved_under_two_keys_enters_once_and_exact_request_wins(
    filled: Stores,
) -> None:
    """Запрос на 12:30, на который источник ответил выпуском 00:30, не должен давать дубль."""
    raw_store, lake_store = filled
    _save(raw_store, fixture_bytes(DISCUSSION), datetime(2026, 1, 15, 12, 30, tzinfo=UTC))

    corpus, report = build_corpus(raw_store, lake_store)

    assert len(corpus) == 2
    assert report.duplicates == 1
    record = corpus.get(DISCUSSION_AT)
    assert record is not None
    assert record.raw_key == "live/2026-01-15/0030/text_nolive.json"


def test_unusable_bodies_are_skipped_with_a_reason_not_fatal(
    filled: Stores, tmp_path: Path
) -> None:
    raw_store, lake_store = filled
    _save(raw_store, "это не json".encode(), datetime(2026, 1, 15, 6, 0, tzinfo=UTC))
    _save(raw_store, fixture_bytes(DISCUSSION), datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    # тело повреждено после записи: хэш расходится с манифестом
    (tmp_path / "raw" / "live" / "2026-01-15" / "0300" / "text_nolive.json").write_bytes(b"{}")
    _save(raw_store, fixture_bytes(DISCUSSION), datetime(2026, 1, 15, 9, 0, tzinfo=UTC))
    # объект пропал, а манифест о нём помнит
    (tmp_path / "raw" / "live" / "2026-01-15" / "0900" / "text_nolive.json").unlink()

    corpus, report = build_corpus(raw_store, lake_store)

    assert len(corpus) == 2
    reasons = dict(report.skipped)
    assert "не разобрано" in reasons["live/2026-01-15/0600/text_nolive.json"]
    assert "хэш" in reasons["live/2026-01-15/0300/text_nolive.json"]
    assert "тела нет" in reasons["live/2026-01-15/0900/text_nolive.json"]


def test_data_bodies_and_foreign_keys_do_not_disturb_the_build(filled: Stores) -> None:
    raw_store, lake_store = filled
    RawStore(raw_store).save_snapshot(
        endpoint="data",
        body=b"{}",
        requested_at=RSGA_REQUEST,
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )
    raw_store.put("_manifest/garbage.json", b"{}")

    corpus, report = build_corpus(raw_store, lake_store)

    assert len(corpus) == 2
    assert [key for key, _ in report.skipped] == ["_manifest/garbage.json"]


def test_empty_raw_layer_gives_an_empty_index(stores: Stores) -> None:
    raw_store, lake_store = stores
    corpus, report = build_corpus(raw_store, lake_store)
    assert len(corpus) == 0 and report.records == 0
    assert corpus.stats() == {"records": 0}
    assert Corpus.load(lake_store).records == ()


# --- Доступ к корпусу ---


@pytest.fixture
def corpus(filled: Stores) -> Corpus:
    return build_corpus(*filled)[0]


def test_select_filters_by_kind_cycle_and_dates(corpus: Corpus) -> None:
    assert [r.kind for r in corpus.select(kind="rsga")] == ["rsga"]
    assert [r.cycle for r in corpus.select(cycle="0030")] == ["0030"]
    assert len(corpus.select(start=date(2026, 1, 15), end=date(2026, 1, 15))) == 2
    assert corpus.select(start=date(2026, 1, 16)) == []
    assert corpus.select(end=date(2026, 1, 14)) == []


def test_get_finds_an_issue_by_its_exact_time(corpus: Corpus) -> None:
    assert corpus.get(DISCUSSION_AT) is not None
    assert corpus.get(datetime(2026, 1, 15, 0, 31, tzinfo=UTC)) is None


def test_sections_yield_text_of_one_section_across_issues(corpus: Corpus) -> None:
    summaries = list(corpus.sections("Solar Activity", part="24 hr Summary"))
    assert [r.kind for r, _ in summaries] == ["discussion"]
    assert summaries[0][1].startswith("Solar activity")
    assert [r.kind for r, _ in corpus.sections("f107")] == ["rsga"]
    assert list(corpus.sections("f107", kind="discussion")) == []
    assert list(corpus.sections("нет такого")) == []


def test_search_returns_matching_lines_with_their_issue(corpus: Corpus) -> None:
    everywhere = {r.kind for r, _ in corpus.search(r"solar activity (was|is)")}
    assert everywhere == {"discussion", "rsga"}  # фраза есть и в Discussion, и в RSGA
    hits = list(corpus.search(r"solar activity (was|is)", kind="discussion"))
    assert hits and all(r.kind == "discussion" for r, _ in hits)
    assert list(corpus.search("PENTICTON", kind="rsga"))  # без учёта регистра
    assert list(corpus.search("PENTICTON", ignore_case=False)) == []


def test_sentences_of_a_section_and_of_the_whole_issue(corpus: Corpus) -> None:
    record = corpus.get(DISCUSSION_AT)
    assert record is not None
    first = corpus.sentences(record, "Solar Activity", "24 hr Summary")[0]
    assert first.startswith("Solar activity")
    assert len(corpus.sentences(record)) > len(corpus.sentences(record, "Geospace", "Forecast"))
    assert corpus.sentences(record, "нет такого") == []


def test_missing_issues_follow_the_source_schedule(corpus: Corpus) -> None:
    # за сутки есть Discussion 00:30 и RSGA 22:00; нет Discussion 12:30
    assert corpus.missing_issues() == [(date(2026, 1, 15), "discussion 1230")]
    assert corpus.missing_issues(date(2026, 1, 16), date(2026, 1, 16)) == [
        (date(2026, 1, 16), "discussion 0030"),
        (date(2026, 1, 16), "discussion 1230"),
        (date(2026, 1, 16), "rsga 2200"),
    ]


def test_stats_summarize_the_corpus(corpus: Corpus) -> None:
    stats = corpus.stats()
    assert stats["records"] == 2
    assert stats["by_kind"] == {"discussion": 1, "rsga": 1}
    assert stats["by_cycle"] == {"discussion 0030": 1, "rsga 2200": 1}
    assert stats["days"] == 1
    assert stats["missing_expected"] == 1
    assert stats["records_with_problems"] == 0


def test_load_without_index_says_how_to_build_it(stores: Stores) -> None:
    with pytest.raises(CorpusError, match="heliogeo corpus build"):
        Corpus.load(stores[1])


def test_corrupt_index_line_is_a_corpus_error() -> None:
    with pytest.raises(CorpusError, match="строка 1"):
        Corpus.from_bytes("не json\n".encode())
