"""Тесты HTTP-клиента swx."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError

SAMPLE_SNAPSHOT = {
    "query_time": "2026-09-01T12:00:00+00:00",
    "active_regions": [],
    "flares": [],
    "cmes": [],
    "particles": {},
    "solar_wind": {},
    "geomag": {},
    "sources": [],
    "notes": [],
}

SAMPLE_TEXT = {
    "query_time": "2026-09-01T12:00:00+00:00",
    "product": "forecast_discussion",
    "kind": "discussion",
    "cycle": "0030",
    "text": "...",
}

MSK = timezone(timedelta(hours=3))


def test_client_default_base_url() -> None:
    """По умолчанию base_url = 503.ifz.ru/swx."""
    client = SwxClient()
    assert client._base_url == "https://503.ifz.ru/swx"


def test_client_custom_base_url() -> None:
    """base_url можно переопределить."""
    client = SwxClient(base_url="https://test.local")
    assert client._base_url == "https://test.local"


# --- Формат момента запроса ---


def test_format_time_now() -> None:
    """query_time=None → 'now'."""
    client = SwxClient()
    assert client._format_time(None) == "now"


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


def test_get_snapshot_rejects_naive_datetime() -> None:
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get") as get, pytest.raises(ValueError, match="часовым поясом"):
        client.get_snapshot(datetime(2026, 9, 1, 12))
    get.assert_not_called()


def test_get_text_rejects_naive_datetime() -> None:
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get") as get, pytest.raises(ValueError, match="часовым поясом"):
        client.get_text(datetime(2026, 9, 1, 12))
    get.assert_not_called()


# --- Запросы ---


def test_get_snapshot_success() -> None:
    """get_snapshot возвращает dict."""
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get", return_value=SAMPLE_SNAPSHOT):
        result = client.get_snapshot(datetime(2026, 9, 1, tzinfo=UTC))
    assert result["query_time"] == "2026-09-01T12:00:00+00:00"


def test_get_snapshot_with_window() -> None:
    """get_snapshot передаёт window."""
    client = SwxClient(base_url="https://test.local")
    captured: dict = {}

    def fake_get(path: str, params: dict | None = None) -> dict:
        captured["path"] = path
        captured["params"] = params
        return SAMPLE_SNAPSHOT

    with patch.object(client, "_get", side_effect=fake_get):
        client.get_snapshot(window="15m", no_live=True)

    assert captured["params"]["window"] == "15m"
    assert captured["params"]["no_live"] == "true"


def test_get_snapshot_path_has_the_time() -> None:
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get", return_value=SAMPLE_SNAPSHOT) as get:
        client.get_snapshot(datetime(2026, 9, 1, 0, 30, tzinfo=UTC), no_live=True)

    assert get.call_args.args == ("/data/2026-09-01T00:30",)
    assert get.call_args.kwargs == {"params": {"no_live": "true"}}


def test_get_text_path_has_the_time() -> None:
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get", return_value=SAMPLE_TEXT) as get:
        client.get_text(datetime(2026, 9, 1, 12, 30, tzinfo=UTC))

    assert get.call_args.args == ("/text/2026-09-01T12:30",)


def test_get_snapshot_now_uses_now_path() -> None:
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get", return_value=SAMPLE_SNAPSHOT) as get:
        client.get_snapshot()

    assert get.call_args.args == ("/data/now",)


def test_get_snapshot_http_error() -> None:
    """HTTP ошибка → SwxClientError."""
    client = SwxClient(base_url="https://test.local")
    with (
        patch.object(client, "_get", side_effect=httpx.ConnectError("no connection")),
        pytest.raises(SwxClientError, match="Failed to get snapshot"),
    ):
        client.get_snapshot()


def test_get_text_success() -> None:
    """get_text возвращает dict."""
    client = SwxClient(base_url="https://test.local")
    with patch.object(client, "_get", return_value=SAMPLE_TEXT):
        result = client.get_text(datetime(2026, 9, 1, tzinfo=UTC))
    assert result["kind"] == "discussion"


def test_get_text_http_error() -> None:
    """HTTP ошибка → SwxClientError."""
    client = SwxClient(base_url="test")
    with (
        patch.object(client, "_get", side_effect=httpx.HTTPError("error")),
        pytest.raises(SwxClientError, match="Failed to get text"),
    ):
        client.get_text()


# --- Повторы и ошибки HTTP (через подмену транспорта, без сети) ---


class _Server:
    """Подменяет httpx.Client транспортом, который отвечает по списку и считает запросы."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)
        real_client = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            reply = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
            if isinstance(reply, Exception):
                raise reply
            return reply

        def fake_client(**kwargs: Any) -> httpx.Client:
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("heliogeo.extractors.swx.client.httpx.Client", fake_client)
        # повторы без реальных пауз
        monkeypatch.setattr(SwxClient._get.retry, "sleep", lambda seconds: None)  # type: ignore[attr-defined]


def _ok() -> httpx.Response:
    return httpx.Response(200, json=SAMPLE_SNAPSHOT)


def _client() -> SwxClient:
    return SwxClient(base_url="https://test.local")


MOMENT = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize("status", [404, 400, 401, 403, 422])
def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """4xx (в том числе 404: за момент нет данных) повтор не лечит."""
    server = _Server(monkeypatch, [httpx.Response(status)])

    with pytest.raises(SwxClientError, match=f"{status}"):
        _client().get_snapshot(MOMENT)

    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
def test_transient_http_statuses_are_retried_three_times(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    server = _Server(monkeypatch, [httpx.Response(status)])

    with pytest.raises(SwxClientError, match=f"{status}"):
        _client().get_snapshot(MOMENT)

    assert len(server.requests) == 3


def test_retry_recovers_after_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(monkeypatch, [httpx.Response(503), httpx.Response(502), _ok()])

    result = _client().get_snapshot(MOMENT)

    assert result["query_time"] == "2026-09-01T12:00:00+00:00"
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
    server = _Server(monkeypatch, [error])

    with pytest.raises(SwxClientError):
        _client().get_snapshot(MOMENT)

    assert len(server.requests) == 3


def test_request_goes_to_the_timed_path_with_params(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(monkeypatch, [_ok()])

    _client().get_snapshot(datetime(2026, 9, 1, 0, 30, tzinfo=UTC), window="1d", no_live=True)

    request = server.requests[0]
    assert request.url.path == "/data/2026-09-01T00:30"
    assert dict(request.url.params) == {"window": "1d", "no_live": "true"}


def test_text_request_goes_to_the_timed_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(monkeypatch, [httpx.Response(200, json=SAMPLE_TEXT)])

    _client().get_text(datetime(2026, 9, 1, 12, 30, tzinfo=UTC))

    assert server.requests[0].url.path == "/text/2026-09-01T12:30"


def test_invalid_json_is_a_client_error_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _Server(monkeypatch, [httpx.Response(200, text="<html>maintenance</html>")])

    with pytest.raises(SwxClientError, match="not valid JSON"):
        _client().get_snapshot(MOMENT)

    assert len(server.requests) == 1


def test_non_object_json_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(monkeypatch, [httpx.Response(200, json=[1, 2, 3])])

    with pytest.raises(SwxClientError, match="Unexpected response type: list"):
        _client().get_snapshot(MOMENT)

    assert len(server.requests) == 1
