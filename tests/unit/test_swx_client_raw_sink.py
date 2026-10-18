"""Клиент источника сохраняет тело успешного ответа как получено и не сохраняет остальное."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.mirror.raw import RawRecord, RawStore
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_TEXT,
    FakeSwxServer,
    fixture_bytes,
    fixture_response,
    json_response,
)


class _Sink:
    """Запоминает вызовы и сохраняет тело в настоящий ``RawStore``."""

    def __init__(self, raw: RawStore) -> None:
        self.raw = raw
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        endpoint: Literal["data", "text"],
        body: bytes,
        requested_at: datetime,
        window: str | None,
        no_live: bool,
        fetched_at: datetime,
    ) -> RawRecord:
        self.calls.append({"endpoint": endpoint, "body": body, "window": window})
        return self.raw.save_snapshot(
            endpoint=endpoint,
            body=body,
            requested_at=requested_at,
            window=window,
            no_live=no_live,
            fetched_at=fetched_at,
        )


@pytest.fixture
def objects(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "raw")


@pytest.fixture
def sink(objects: LocalObjectStore) -> _Sink:
    return _Sink(RawStore(objects))


def _client(sink: _Sink | None) -> SwxClient:
    return SwxClient(
        base_url="https://swx.test",
        clock=lambda: FETCHED_AT,
        raw_sink=sink,  # type: ignore[arg-type]
    )


def test_the_data_body_is_saved_byte_for_byte_and_the_response_knows_where(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink, objects: LocalObjectStore
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client(sink).get_data(QUIET_END_OF_DAY, no_live=True)

    key = "live/2026-09-01/2359/data_ut_day_nolive.json"
    assert objects.get(key) == fixture_bytes(QUIET_DATA)
    assert response.raw_key == key
    assert response.raw_sha256 == objects.head(key).sha256  # type: ignore[union-attr]
    assert sink.calls[0]["endpoint"] == "data"


def test_the_text_body_is_saved_too(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink, objects: LocalObjectStore
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    response = _client(sink).get_text(QUIET_END_OF_DAY, no_live=True)

    assert response.raw_key == "live/2026-09-01/2359/text_nolive.json"
    assert objects.get(response.raw_key) == fixture_bytes(QUIET_TEXT)


def test_the_window_and_live_mode_are_part_of_the_key(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client(sink).get_data(QUIET_END_OF_DAY, window="15m", no_live=False)

    assert response.raw_key == "live/2026-09-01/2359/data_15m_live.json"


def test_a_repeated_request_leaves_the_stored_object_unchanged(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])
    client = _client(sink)

    first = client.get_data(QUIET_END_OF_DAY, no_live=True)
    second = client.get_data(QUIET_END_OF_DAY, no_live=True)

    assert first.raw_sha256 == second.raw_sha256
    assert sink.raw.load_manifest(QUIET_END_OF_DAY.date()).entries[0].replaced_sha256 == ()
    assert len(sink.calls) == 2


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, content=b"<html>error</html>"),
        httpx.Response(200, content=b"[1, 2, 3]"),
        json_response({"error": "bad date"}),
        httpx.Response(404, content=b"{}"),
        httpx.Response(503, content=b"{}"),
    ],
)
def test_a_body_that_is_not_a_valid_answer_is_not_saved(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink, objects: LocalObjectStore, reply: httpx.Response
) -> None:
    FakeSwxServer(monkeypatch, [reply])

    with pytest.raises(SwxClientError):
        _client(sink).get_data(QUIET_END_OF_DAY, no_live=True)

    assert sink.calls == []
    assert objects.list_keys() == []


def test_without_a_sink_nothing_is_saved_and_the_response_has_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client(None).get_data(QUIET_END_OF_DAY, no_live=True)

    assert (response.raw_key, response.raw_sha256) == (None, None)


def test_a_failure_of_the_sink_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    def broken(**_: Any) -> RawRecord:
        raise OSError("диск недоступен")

    with pytest.raises(OSError, match="диск"):
        _client(broken).get_data(QUIET_END_OF_DAY, no_live=True)  # type: ignore[arg-type]


def test_the_sink_receives_what_is_needed_to_restore_the_response(
    monkeypatch: pytest.MonkeyPatch, sink: _Sink
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    _client(sink).get_data(QUIET_END_OF_DAY, no_live=True)

    entry = sink.raw.load_manifest(QUIET_END_OF_DAY.date()).entries[0]
    assert entry.requested_at == datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
    assert entry.fetched_at == FETCHED_AT
    assert entry.no_live is True
