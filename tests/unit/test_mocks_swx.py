"""Проверка моков источника SWX: фикстуры, готовые ответы клиента, подставной сервер."""

import time
from datetime import UTC, datetime

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from tests.mocks.swx import (
    FETCHED_AT,
    FIXTURES,
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_NOON,
    QUIET_TEXT,
    STORM_DATA,
    STORM_NOON,
    STORM_TEXT,
    FakeSwxServer,
    answered_for,
    fixture_bytes,
    fixture_response,
    json_response,
    load_fixture,
    make_data_response,
    make_text_response,
)


def test_all_four_fixtures_exist() -> None:
    for name in (QUIET_DATA, STORM_DATA, QUIET_TEXT, STORM_TEXT):
        assert (FIXTURES / name).is_file()


def test_load_fixture_returns_a_fresh_copy_each_time() -> None:
    first = load_fixture(QUIET_DATA)
    first["flares"].clear()

    assert len(load_fixture(QUIET_DATA)["flares"]) == 8


def test_fixture_bytes_are_the_file_as_is() -> None:
    assert fixture_bytes(QUIET_DATA) == (FIXTURES / QUIET_DATA).read_bytes()
    assert fixture_response(QUIET_DATA).json() == load_fixture(QUIET_DATA)


def test_json_response() -> None:
    response = json_response({"a": 1}, status=201)

    assert response.status_code == 201
    assert response.json() == {"a": 1}


def test_answered_for_copies_and_replaces_the_query_time() -> None:
    raw = load_fixture(QUIET_DATA)

    body = answered_for(raw, QUIET_END_OF_DAY)

    assert body["query_time"] == "2026-09-01T23:59:00+00:00"
    assert raw["query_time"] == "2026-09-01T12:00:00+00:00"
    assert body["flares"] is not raw["flares"]


def test_default_data_response_is_the_quiet_day_at_noon() -> None:
    response = make_data_response()

    assert response.requested_at == QUIET_NOON
    assert response.fetched_at == FETCHED_AT
    assert response.problems == ()
    assert len(response.data["flares"]) == 1
    assert len(response.raw["flares"]) == 8


def test_data_response_for_another_moment_reports_the_mismatch_unless_told_to_follow() -> None:
    mismatch = make_data_response(at=QUIET_END_OF_DAY)
    followed = make_data_response(at=QUIET_END_OF_DAY, follow_request=True)

    assert len(mismatch.problems) == 1
    assert "source answered for query_time" in mismatch.problems[0]
    assert followed.problems == ()
    assert followed.query_time == QUIET_END_OF_DAY
    assert len(followed.data["flares"]) == 8


def test_data_response_keeps_window_and_no_live() -> None:
    response = make_data_response(window="1d", no_live=True)

    assert response.window_spec == "1d"
    assert response.no_live is True


def test_data_response_from_a_storm_fixture() -> None:
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_NOON)

    assert response.problems == ()
    assert len(response.raw["flares"]) == 11


def test_default_text_response_is_the_quiet_day() -> None:
    response = make_text_response()

    assert response.kind == "discussion"
    assert response.requested_at == QUIET_NOON
    assert response.problems == ()


def test_text_response_follow_request() -> None:
    mismatch = make_text_response(at=QUIET_END_OF_DAY)
    followed = make_text_response(at=QUIET_END_OF_DAY, follow_request=True)

    assert len(mismatch.problems) == 1
    assert followed.problems == ()


# --- Подставной сервер ---


def _client() -> SwxClient:
    return SwxClient(base_url="https://test.local")


def test_server_answers_in_order_and_repeats_the_last(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(
        monkeypatch, [fixture_response(QUIET_DATA), fixture_response(STORM_DATA)]
    )
    client = _client()

    first = client.get_data(QUIET_NOON)
    second = client.get_data(STORM_NOON)
    third = client.get_data(STORM_NOON)

    assert first.raw == load_fixture(QUIET_DATA)
    assert second.raw == third.raw == load_fixture(STORM_DATA)
    assert len(server.requests) == 3


def test_server_raises_exceptions_from_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [httpx.ConnectError("down")])

    with pytest.raises(SwxClientError):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 3  # клиент повторил: сетевая ошибка временная


def test_server_records_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    _client().get_text(datetime(2026, 9, 1, 12, 30, tzinfo=UTC), no_live=True)

    request = server.requests[0]
    assert request.url.path == "/text/2026-09-01T12:30"
    assert dict(request.url.params) == {"no_live": "true"}
    assert request.url.host == "test.local"


def test_server_disables_the_pauses_between_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [httpx.Response(503)])
    started = time.monotonic()

    with pytest.raises(SwxClientError):
        _client().get_data(QUIET_NOON)

    assert time.monotonic() - started < 1.0  # настоящие паузы заняли бы около трёх секунд
