"""Бэкфилл: данные и тексты за период на подставном сервере (сеть не нужна)."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient
from heliogeo.functions.backfill import (
    DISCUSSION_TIMES,
    BackfillReport,
    DayResult,
    ThrottledSink,
    backfill,
    days_between,
    normalize_days,
)
from heliogeo.mirror.raw import RawStore
from heliogeo.storage.keys import key_l1
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    FakeSwxServer,
    answered_for,
    json_response,
    load_fixture,
)

DAY = date(2026, 9, 1)
END_OF_DAY = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
RUN_ID = "backfill/test"
DISCUSSION = "swx_text_discussion_2026-01-15_0030.json"
RSGA = "swx_text_rsga_2026-01-15.json"


def _data() -> httpx.Response:
    return json_response(answered_for(load_fixture(QUIET_DATA), END_OF_DAY))


def _issue(name: str, issued: datetime) -> httpx.Response:
    """Ответ /text с выпуском, перенесённым на ``issued``."""
    body = load_fixture(name)
    body["issued_dt"] = issued.isoformat()
    body["cycle"] = f"{issued:%H%M}"
    body["issued"] = f"{issued:%Y %b %d %H%M} UTC"
    return json_response(body)


def _day_responses() -> list[httpx.Response | Exception]:
    """Что отвечает источник на запросы одних суток, в порядке запросов бэкфилла."""
    return [
        _data(),  # /data на 23:59
        _issue(RSGA, datetime(2026, 9, 1, 22, 0, tzinfo=UTC)),  # /text на 23:59 → RSGA 22:00
        _issue(DISCUSSION, datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),  # /text на 00:30
        _issue(DISCUSSION, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)),  # /text на 12:30
    ]


@pytest.fixture
def raw_store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "raw")


@pytest.fixture
def lake_store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "lake")


class Sleeps(list[float]):
    """Записывает паузы вместо ожидания."""

    def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def client_factory(sleeps: Sleeps):  # type: ignore[no-untyped-def]
    def factory(raw: RawStore) -> SwxClient:
        sink = ThrottledSink(raw.save_snapshot, 0.4, sleeps)
        return SwxClient(base_url="https://swx.test", clock=lambda: FETCHED_AT, raw_sink=sink)

    return factory


def _run(
    days: list[date],
    raw_store: LocalObjectStore,
    client_factory: Any,
    **kwargs: Any,
) -> BackfillReport:
    return backfill(days, raw_store, client_factory=client_factory, run_id=RUN_ID, **kwargs)


# --- Период ---


def test_days_between_is_inclusive_and_ordered() -> None:
    assert days_between(date(2026, 2, 27), date(2026, 3, 2)) == [
        date(2026, 2, 27),
        date(2026, 2, 28),
        date(2026, 3, 1),
        date(2026, 3, 2),
    ]
    assert days_between(DAY, DAY) == [DAY]


def test_days_between_rejects_a_reversed_period() -> None:
    with pytest.raises(ValueError, match="раньше начала"):
        days_between(date(2026, 9, 2), DAY)


# --- Пауза между запросами ---


def test_throttled_sink_pauses_after_each_saved_response(
    raw_store: LocalObjectStore, sleeps: Sleeps
) -> None:
    sink = ThrottledSink(RawStore(raw_store).save_snapshot, 0.4, sleeps)
    for hour in (1, 2):
        sink(
            endpoint="text",
            body=b"{}",
            requested_at=datetime(2026, 9, 1, hour, 0, tzinfo=UTC),
            window=None,
            no_live=True,
            fetched_at=FETCHED_AT,
        )
    assert sleeps == [0.4, 0.4]


def test_zero_delay_does_not_sleep(raw_store: LocalObjectStore, sleeps: Sleeps) -> None:
    sink = ThrottledSink(RawStore(raw_store).save_snapshot, 0, sleeps)
    sink(
        endpoint="text",
        body=b"{}",
        requested_at=END_OF_DAY,
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )
    assert sleeps == []


# --- Сбор ---


def test_one_day_brings_data_and_all_three_text_issues(
    monkeypatch: pytest.MonkeyPatch,
    raw_store: LocalObjectStore,
    client_factory: Any,
    sleeps: Sleeps,
) -> None:
    server = FakeSwxServer(monkeypatch, _day_responses())

    report = _run([DAY], raw_store, client_factory)

    assert [r.ok for r in report.results] == [True]
    assert report.results[0].from_raw is False
    assert report.stopped_early is False
    assert sorted(raw_store.list_keys("live/")) == [
        "live/2026-09-01/0030/text_nolive.json",
        "live/2026-09-01/1230/text_nolive.json",
        "live/2026-09-01/2359/data_ut_day_nolive.json",
        "live/2026-09-01/2359/text_nolive.json",
    ]
    assert [r.url.path for r in server.requests] == [
        "/data/2026-09-01T23:59",
        "/text/2026-09-01T23:59",
        "/text/2026-09-01T00:30",
        "/text/2026-09-01T12:30",
    ]
    assert len(sleeps) == 4  # пауза после каждого из четырёх ответов


def test_discussion_slots_are_the_two_cycles_the_evening_query_cannot_return() -> None:
    assert [f"{slot:%H%M}" for slot in DISCUSSION_TIMES] == ["0030", "1230"]


def test_second_run_over_the_same_period_does_not_touch_the_network(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    FakeSwxServer(monkeypatch, _day_responses())
    _run([DAY], raw_store, client_factory)

    offline = FakeSwxServer(monkeypatch, [httpx.ConnectTimeout("сети нет")])
    report = _run([DAY], raw_store, client_factory)

    assert offline.requests == []
    assert report.results[0].ok and report.results[0].from_raw is True


def test_missing_discussion_issue_is_a_note_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    """На 00:30 источник ответил вчерашним вечерним выпуском: выпуск пропущен, день не потерян."""
    FakeSwxServer(
        monkeypatch,
        [
            _data(),
            _issue(RSGA, datetime(2026, 9, 1, 22, 0, tzinfo=UTC)),
            _issue(RSGA, datetime(2026, 8, 31, 22, 0, tzinfo=UTC)),  # на 00:30 ответил вчерашний
            _issue(DISCUSSION, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)),
        ],
    )

    result = _run([DAY], raw_store, client_factory).results[0]

    assert result.ok
    assert any("выпуска 00:30 нет" in note for note in result.notes)


def test_unavailable_discussion_is_a_note_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    FakeSwxServer(
        monkeypatch,
        [
            _data(),
            _issue(RSGA, datetime(2026, 9, 1, 22, 0, tzinfo=UTC)),
            httpx.Response(404, content=b"{}"),
            httpx.Response(404, content=b"{}"),
        ],
    )

    result = _run([DAY], raw_store, client_factory).results[0]

    assert result.ok
    assert [note for note in result.notes if "не получен" in note]


# --- Сбои ---


def test_day_without_data_fails_but_the_period_goes_on(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    """404 по /data — дыра в архиве: она не должна останавливать бэкфилл."""
    FakeSwxServer(monkeypatch, [httpx.Response(404, content=b"{}")])

    report = _run([DAY, date(2026, 9, 2), date(2026, 9, 3)], raw_store, client_factory)

    assert [r.ok for r in report.results] == [False, False, False]
    assert report.stopped_early is False
    assert all(r.retryable is False for r in report.results)
    assert "DATA_INSUFFICIENT" in (report.results[0].error or "")
    assert report.failed_days == [DAY, date(2026, 9, 2), date(2026, 9, 3)]


def test_circuit_breaker_stops_after_repeated_unavailability(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    FakeSwxServer(monkeypatch, [httpx.ConnectTimeout("лежит")])
    days = days_between(DAY, date(2026, 9, 10))

    report = _run(days, raw_store, client_factory, max_consecutive_failures=2)

    assert report.stopped_early is True
    assert len(report.results) == 2  # остальные восемь суток не тронуты
    assert all(r.retryable for r in report.results)
    assert "SOURCE_UNAVAILABLE" in (report.results[0].error or "")


def test_a_good_day_resets_the_failure_streak(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    """Сбои вперемешку с удачными сутками не должны останавливать бэкфилл."""
    down = httpx.ConnectTimeout("лежит")
    # у клиента три попытки на запрос: сутки 1 — три отказа подряд, 2 — успех, 3 — снова отказы
    FakeSwxServer(monkeypatch, [down, down, down, *_day_responses(), down])

    report = _run(
        days_between(DAY, date(2026, 9, 3)), raw_store, client_factory, max_consecutive_failures=2
    )

    assert [r.ok for r in report.results] == [False, True, False]
    assert report.stopped_early is False


def test_progress_callback_sees_every_processed_day(
    monkeypatch: pytest.MonkeyPatch, raw_store: LocalObjectStore, client_factory: Any
) -> None:
    FakeSwxServer(monkeypatch, _day_responses())
    seen: list[DayResult] = []

    _run([DAY], raw_store, client_factory, on_day=seen.append)

    assert [r.day for r in seen] == [DAY]


# --- Нормализация ---


def test_normalize_turns_stored_days_into_l1_and_reports_gaps(
    monkeypatch: pytest.MonkeyPatch,
    raw_store: LocalObjectStore,
    lake_store: LocalObjectStore,
    client_factory: Any,
) -> None:
    FakeSwxServer(monkeypatch, _day_responses())
    _run([DAY], raw_store, client_factory)

    report = normalize_days([DAY, date(2026, 9, 2)], raw_store, lake_store, run_id=RUN_ID)

    assert report.ok_days == [DAY]
    assert report.failed_days == [date(2026, 9, 2)]  # данных за эти сутки не скачивали
    assert "DATA_INSUFFICIENT" in (report.results[1].error or "")
    assert lake_store.head(key_l1("flares", DAY)) is not None
    assert lake_store.head(key_l1("geomag_obs", DAY)) is not None
