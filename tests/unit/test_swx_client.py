"""Тесты HTTP-клиента swx."""

from datetime import UTC, datetime
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


def test_client_default_base_url() -> None:
    """По умолчанию base_url = 503.ifz.ru/swx."""
    client = SwxClient()
    assert client._base_url == "https://503.ifz.ru/swx"


def test_client_custom_base_url() -> None:
    """base_url можно переопределить."""
    client = SwxClient(base_url="https://test.local")
    assert client._base_url == "https://test.local"


def test_format_date_now() -> None:
    """query_time=None → 'now'."""
    client = SwxClient()
    assert client._format_date(None) == "now"


def test_format_date_specific() -> None:
    """query_time → YYYY-MM-DD."""
    client = SwxClient()
    dt = datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert client._format_date(dt) == "2026-09-01"


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
