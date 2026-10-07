"""Тесты HTTP-клиента swx: запросы, повторы, типизированные ответы (без сети)."""

import logging
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.response import SwxDataResponse, SwxTextResponse
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_NOON,
    QUIET_TEXT,
    STORM_DATA,
    STORM_NOON,
    FakeSwxServer,
    answered_for,
    fixture_response,
    json_response,
    load_fixture,
)

MSK = timezone(timedelta(hours=3))
LOGGER = "heliogeo.extractors.swx.client"


def _client(**kwargs: object) -> SwxClient:
    return SwxClient(base_url="https://test.local", clock=lambda: FETCHED_AT, **kwargs)  # type: ignore[arg-type]


def test_client_default_base_url() -> None:
    """По умолчанию base_url = 503.ifz.ru/swx."""
    client = SwxClient()
    assert client._base_url == "https://503.ifz.ru/swx"


def test_client_custom_base_url() -> None:
    """base_url можно переопределить."""
    client = SwxClient(base_url="https://test.local")
    assert client._base_url == "https://test.local"


# --- Момент запроса ---


def test_format_time_specific() -> None:
    """query_time → YYYY-MM-DDTHH:MM: время передаётся всегда."""
    client = SwxClient()
    assert client._format_time(datetime(2026, 9, 1, 12, tzinfo=UTC)) == "2026-09-01T12:00"
    assert client._format_time(datetime(2026, 9, 1, 0, 30, tzinfo=UTC)) == "2026-09-01T00:30"


def test_format_time_midnight_keeps_the_time() -> None:
    """Полночь не превращается в «просто дату»: источник прочёл бы её как 12:00."""
    client = SwxClient()
    assert client._format_time(datetime(2026, 9, 1, tzinfo=UTC)) == "2026-09-01T00:00"


def test_format_time_converts_to_utc() -> None:
    client = SwxClient()
    assert client._format_time(datetime(2026, 9, 1, 3, 30, tzinfo=MSK)) == "2026-09-01T00:30"
    # смещение переводит момент в другие сутки
    assert client._format_time(datetime(2026, 9, 2, 1, 0, tzinfo=MSK)) == "2026-09-01T22:00"


def test_format_time_truncates_seconds() -> None:
    client = SwxClient()
    assert client._format_time(datetime(2026, 9, 1, 12, 0, 59, tzinfo=UTC)) == "2026-09-01T12:00"


def test_format_time_rejects_naive_datetime() -> None:
    """Наивное время сдвигает момент на пояс машины: запрещено."""
    client = SwxClient()
    with pytest.raises(ValueError, match="часовым поясом"):
        client._format_time(datetime(2026, 9, 1, 12))


def test_resolve_moment_floors_to_the_minute_and_converts_to_utc() -> None:
    client = SwxClient()

    moment = client.resolve_moment(datetime(2026, 9, 1, 15, 0, 45, 123456, tzinfo=MSK))

    assert moment == QUIET_NOON
    assert moment.tzinfo is UTC


def test_resolve_moment_none_means_now_by_the_client_clock() -> None:
    client = SwxClient(clock=lambda: datetime(2026, 9, 1, 12, 0, 45, 999, tzinfo=UTC))

    assert client.resolve_moment(None) == QUIET_NOON


def test_resolve_moment_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        SwxClient().resolve_moment(datetime(2026, 9, 1, 12))


# --- get_data ---


def test_get_data_returns_a_typed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client().get_data(QUIET_NOON)

    assert isinstance(response, SwxDataResponse)
    assert response.requested_at == QUIET_NOON
    assert response.query_time == QUIET_NOON
    assert response.raw == load_fixture(QUIET_DATA)
    assert len(response.sources) == 11
    assert response.fetched_at == FETCHED_AT
    assert response.from_mirror is False
    assert response.raw_sha256 is None
    assert response.problems == ()
    assert response.unknown_fields == ()


def test_get_data_brings_the_response_to_the_requested_moment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client().get_data(QUIET_NOON)

    assert len(response.raw["flares"]) == 8
    assert len(response.data["flares"]) == 1
    assert response.events_after_query == 9
    assert response.availability.dropped_forecasts == ("three_day_forecast", "daypre")


def test_get_data_at_the_end_of_the_day_keeps_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Источник отвечает на запрошенный момент: query_time в теле равен запрошенному."""
    body = answered_for(load_fixture(QUIET_DATA), QUIET_END_OF_DAY)
    FakeSwxServer(monkeypatch, [json_response(body)])

    response = _client().get_data(QUIET_END_OF_DAY)

    assert response.events_after_query == 0
    assert len(response.data["flares"]) == 8
    assert response.problems == ()


def test_get_data_path_has_the_time_and_no_params_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    _client().get_data(datetime(2026, 9, 1, 0, 30, tzinfo=UTC))

    request = server.requests[0]
    assert request.url.path == "/data/2026-09-01T00:30"
    assert request.url.query == b""


def test_get_data_passes_window_and_no_live(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    response = _client().get_data(QUIET_NOON, window="15m", no_live=True)

    assert dict(server.requests[0].url.params) == {"window": "15m", "no_live": "true"}
    assert response.window_spec == "15m"
    assert response.no_live is True


def test_get_data_now_uses_the_client_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])
    client = SwxClient(
        base_url="https://test.local", clock=lambda: datetime(2026, 9, 1, 12, 0, 45, tzinfo=UTC)
    )

    response = client.get_data()

    assert server.requests[0].url.path == "/data/2026-09-01T12:00"
    assert response.requested_at == QUIET_NOON
    assert response.fetched_at == datetime(2026, 9, 1, 12, 0, 45, tzinfo=UTC)


def test_get_data_rejects_naive_datetime_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])

    with pytest.raises(ValueError, match="часовым поясом"):
        _client().get_data(datetime(2026, 9, 1, 12))

    assert server.requests == []


def test_get_data_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка HTTP → SwxClientError."""
    FakeSwxServer(monkeypatch, [httpx.ConnectError("no connection")])

    with pytest.raises(SwxClientError, match="Failed to get snapshot"):
        _client().get_data(QUIET_NOON)


def test_get_data_rejects_a_body_that_is_not_a_data_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON с ошибкой вместо данных (даже с кодом 200) нельзя принять за данные."""
    server = FakeSwxServer(monkeypatch, [json_response({"detail": "Date out of range"})])

    with pytest.raises(SwxClientError, match="Invalid /data response: /data response has no"):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 1  # не повторяется


# --- get_text ---


def test_get_text_returns_a_typed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    response = _client().get_text(QUIET_NOON)

    assert isinstance(response, SwxTextResponse)
    assert response.kind == "discussion"
    assert response.cycle == "0030"
    assert response.issued_dt == datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    assert len(response.available) == 10
    assert response.requested_at == QUIET_NOON
    assert response.fetched_at == FETCHED_AT
    assert response.problems == ()


def test_get_text_path_has_the_time(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    _client().get_text(datetime(2026, 9, 1, 12, 30, tzinfo=UTC))

    request = server.requests[0]
    assert request.url.path == "/text/2026-09-01T12:30"
    assert request.url.query == b""


def test_get_text_passes_no_live(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    response = _client().get_text(QUIET_NOON, no_live=True)

    assert dict(server.requests[0].url.params) == {"no_live": "true"}
    assert response.no_live is True


def test_get_text_now_uses_the_client_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])
    client = SwxClient(
        base_url="https://test.local", clock=lambda: datetime(2026, 9, 1, 12, 30, 7, tzinfo=UTC)
    )

    client.get_text()

    assert server.requests[0].url.path == "/text/2026-09-01T12:30"


def test_get_text_rejects_naive_datetime_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeSwxServer(monkeypatch, [fixture_response(QUIET_TEXT)])

    with pytest.raises(ValueError, match="часовым поясом"):
        _client().get_text(datetime(2026, 9, 1, 12))

    assert server.requests == []


def test_get_text_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка HTTP → SwxClientError."""
    FakeSwxServer(monkeypatch, [httpx.HTTPError("error")])

    with pytest.raises(SwxClientError, match="Failed to get text"):
        _client().get_text()


def test_get_text_rejects_a_body_that_is_not_a_text_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSwxServer(monkeypatch, [json_response({"detail": "no text"})])

    with pytest.raises(SwxClientError, match="Invalid /text response: /text response has no"):
        _client().get_text(QUIET_NOON)


# --- Повторы и ошибки HTTP (через подмену транспорта, без сети) ---


@pytest.mark.parametrize("status", [404, 400, 401, 403, 422])
def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """4xx (в том числе 404: за момент нет данных) повтор не лечит."""
    server = FakeSwxServer(monkeypatch, [httpx.Response(status)])

    with pytest.raises(SwxClientError, match=f"{status}"):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
def test_transient_http_statuses_are_retried_three_times(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    server = FakeSwxServer(monkeypatch, [httpx.Response(status)])

    with pytest.raises(SwxClientError, match=f"{status}"):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 3


def test_retry_recovers_after_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(
        monkeypatch,
        [httpx.Response(503), httpx.Response(502), fixture_response(QUIET_DATA)],
    )

    response = _client().get_data(QUIET_NOON)

    assert response.query_time == QUIET_NOON
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.ConnectError("c"),
        httpx.ReadError("r"),
    ],
    ids=["connect-timeout", "read-timeout", "connect-error", "read-error"],
)
def test_network_errors_are_retried(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    server = FakeSwxServer(monkeypatch, [error])

    with pytest.raises(SwxClientError):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 3


def test_text_requests_are_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [httpx.Response(503), fixture_response(QUIET_TEXT)])

    response = _client().get_text(QUIET_NOON)

    assert response.kind == "discussion"
    assert len(server.requests) == 2


def test_invalid_json_is_a_client_error_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeSwxServer(monkeypatch, [httpx.Response(200, text="<html>maintenance</html>")])

    with pytest.raises(SwxClientError, match="not valid JSON"):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 1


def test_empty_body_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [httpx.Response(200, content=b"")])

    with pytest.raises(SwxClientError, match="not valid JSON"):
        _client().get_data(QUIET_NOON)


def test_non_object_json_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeSwxServer(monkeypatch, [json_response([1, 2, 3])])

    with pytest.raises(SwxClientError, match="Unexpected response type: list"):
        _client().get_data(QUIET_NOON)

    assert len(server.requests) == 1


# --- Предупреждения о том, что в ответе показалось неправильным ---


def test_problems_of_the_response_are_logged_as_warnings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Источник мог проигнорировать время: об этом клиент предупреждает."""
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])
    caplog.set_level(logging.WARNING, logger=LOGGER)

    _client().get_data(datetime(2026, 9, 1, 0, 30, tzinfo=UTC))

    assert "SWX /data response for 2026-09-01T00:30:00+00:00: source answered for query_time" in (
        caplog.text
    )


def test_no_warnings_for_a_clean_response(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA)])
    caplog.set_level(logging.WARNING, logger=LOGGER)

    _client().get_data(QUIET_NOON)

    assert caplog.records == []


def test_unknown_fields_are_reported_once_per_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    body = load_fixture(QUIET_DATA)
    body["new_section"] = {}
    body["flares"][0]["new_field"] = 1
    FakeSwxServer(monkeypatch, [json_response(body)])
    caplog.set_level(logging.WARNING, logger=LOGGER)
    client = _client()

    first = client.get_data(QUIET_NOON)
    client.get_data(QUIET_NOON)

    assert first.unknown_fields == ("flares[].new_field", "new_section")
    messages = [r.getMessage() for r in caplog.records if "unknown fields" in r.getMessage()]
    assert messages == ["SWX /data response has unknown fields: flares[].new_field, new_section"]


def test_a_new_unknown_field_is_reported_again(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    first_body = load_fixture(QUIET_DATA)
    first_body["new_section"] = {}
    second_body = load_fixture(QUIET_DATA)
    second_body["new_section"] = {}
    second_body["another_section"] = {}
    FakeSwxServer(monkeypatch, [json_response(first_body), json_response(second_body)])
    caplog.set_level(logging.WARNING, logger=LOGGER)
    client = _client()

    client.get_data(QUIET_NOON)
    client.get_data(QUIET_NOON)

    messages = [r.getMessage() for r in caplog.records if "unknown fields" in r.getMessage()]
    assert messages == [
        "SWX /data response has unknown fields: new_section",
        "SWX /data response has unknown fields: another_section",
    ]


def test_unknown_fields_of_data_and_text_are_tracked_separately(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    data_body = load_fixture(QUIET_DATA)
    data_body["revision"] = 1
    text_body = load_fixture(QUIET_TEXT)
    text_body["revision"] = 1
    FakeSwxServer(monkeypatch, [json_response(data_body), json_response(text_body)])
    caplog.set_level(logging.WARNING, logger=LOGGER)
    client = _client()

    client.get_data(QUIET_NOON)
    client.get_text(QUIET_NOON)

    messages = [r.getMessage() for r in caplog.records if "unknown fields" in r.getMessage()]
    assert messages == [
        "SWX /data response has unknown fields: revision",
        "SWX /text response has unknown fields: revision",
    ]


def test_storm_data_and_text_parse_through_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [fixture_response(STORM_DATA)])

    response = _client().get_data(STORM_NOON)

    assert len(response.sources) == 12
    assert response.problems == ()
    assert response.availability.ongoing_flares == ("2024-05-11T11:44:00+00:00 X1.5",)


# --- Вид ошибки: что случилось (по нему пайплайн определяет, повторять ли шаг) ---


@pytest.mark.parametrize(
    "reply,kind",
    [
        (httpx.Response(503), "unavailable"),
        (httpx.Response(502), "unavailable"),
        (httpx.Response(429), "unavailable"),
        (httpx.Response(408), "unavailable"),
        (httpx.ConnectTimeout("t"), "unavailable"),
        (httpx.ConnectError("c"), "unavailable"),
        (httpx.Response(404), "not_found"),
        (httpx.Response(400), "rejected"),
        (httpx.Response(403), "rejected"),
        (httpx.Response(422), "rejected"),
        (httpx.Response(200, text="<html>maintenance</html>"), "malformed"),
        (json_response([1, 2, 3]), "malformed"),
        (json_response({"detail": "Date out of range"}), "malformed"),
    ],
    ids=[
        "503",
        "502",
        "429",
        "408",
        "connect-timeout",
        "connect-error",
        "404",
        "400",
        "403",
        "422",
        "not-json",
        "not-an-object",
        "not-an-endpoint-answer",
    ],
)
def test_client_error_kind(
    monkeypatch: pytest.MonkeyPatch, reply: httpx.Response | Exception, kind: str
) -> None:
    FakeSwxServer(monkeypatch, [reply])

    with pytest.raises(SwxClientError) as caught:
        _client().get_data(QUIET_NOON)

    assert caught.value.kind == kind


def test_client_error_kind_is_the_same_for_text_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSwxServer(monkeypatch, [httpx.Response(404)])

    with pytest.raises(SwxClientError) as caught:
        _client().get_text(QUIET_NOON)

    assert caught.value.kind == "not_found"


def test_client_error_without_a_kind_is_malformed() -> None:
    """Вид по умолчанию — «ответ не тот»: повтор такой ошибке не поможет."""
    assert SwxClientError("boom").kind == "malformed"
