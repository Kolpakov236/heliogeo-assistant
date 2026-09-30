"""Тесты доставки."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from heliogeo.delivery import DeliveryError, TelegramDelivery, get_delivery
from heliogeo.products.base import Product, ProductMetadata


def _product(text: str = "Kp 2.3. Dst -9 nT.") -> Product:
    return Product(
        metadata=ProductMetadata(
            product_type="icao_brief",
            issued_utc=datetime(2026, 9, 1, 12, tzinfo=UTC),
            grounding_ok=True,
            grounding_summary="OK (2 numbers checked)",
        ),
        text=text,
    )


# --- Инициализация ---


def test_telegram_init_dry_run() -> None:
    """dry_run=True работает без токена."""
    d = TelegramDelivery(dry_run=True)
    assert d._dry_run is True


def test_telegram_init_no_token() -> None:
    """Без токена и без dry_run → DeliveryError."""
    with (
        patch(
            "heliogeo.delivery.telegram.get_settings",
            return_value=MagicMock(
                telegram_bot_token="",
                telegram_chat_id="",
            ),
        ),
        pytest.raises(DeliveryError, match="TELEGRAM_BOT_TOKEN"),
    ):
        TelegramDelivery()


def test_telegram_init_no_chat_id() -> None:
    """Без chat_id и без dry_run → DeliveryError."""
    with (
        patch(
            "heliogeo.delivery.telegram.get_settings",
            return_value=MagicMock(
                telegram_bot_token="test-token",
                telegram_chat_id="",
            ),
        ),
        pytest.raises(DeliveryError, match="TELEGRAM_CHAT_ID"),
    ):
        TelegramDelivery()


def test_telegram_init_with_params() -> None:
    """Можно передать token и chat_id явно."""
    d = TelegramDelivery(bot_token="test", chat_id="123")
    assert d._bot_token == "test"
    assert d._chat_id == "123"


# --- Dry run ---


def test_telegram_dry_run_send() -> None:
    """dry_run не делает HTTP-запросов."""
    d = TelegramDelivery(dry_run=True)
    d.send(_product())


# --- Send (мок httpx) ---


def test_telegram_send_success() -> None:
    """send делает два запроса: sendMessage + sendDocument."""
    d = TelegramDelivery(bot_token="test", chat_id="123")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        d.send(_product(), formats=["txt"])

    assert mock_client.post.call_count == 2


def test_telegram_send_txt_pdf() -> None:
    """send с txt+pdf делает 3 запроса."""
    d = TelegramDelivery(bot_token="test", chat_id="123")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        d.send(_product(), formats=["txt", "pdf"])

    assert mock_client.post.call_count == 3


# --- Ошибки ---


def test_telegram_send_http_error() -> None:
    """HTTP ошибка → DeliveryError."""
    d = TelegramDelivery(bot_token="test", chat_id="123")

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("no connection")
        mock_client_class.return_value = mock_client

        with pytest.raises(DeliveryError, match="Telegram delivery failed"):
            d.send(_product())


# --- Фабрика ---


def test_get_delivery_telegram() -> None:
    """get_delivery('telegram') возвращает TelegramDelivery."""
    with patch(
        "heliogeo.delivery.telegram.get_settings",
        return_value=MagicMock(
            telegram_bot_token="test",
            telegram_chat_id="123",
        ),
    ):
        d = get_delivery("telegram")
        assert isinstance(d, TelegramDelivery)


def test_get_delivery_mock() -> None:
    """get_delivery('mock') возвращает dry_run-доставку."""
    d = get_delivery("mock")
    assert isinstance(d, TelegramDelivery)
    assert d._dry_run is True


def test_get_delivery_unknown() -> None:
    """get_delivery('unknown') → ValueError."""
    with pytest.raises(ValueError, match="Unknown delivery"):
        get_delivery("unknown")


# --- Header ---


def test_telegram_header_format() -> None:
    """_build_header формирует заголовок."""
    d = TelegramDelivery(dry_run=True)
    header = d._build_header(_product())
    assert "ICAO_BRIEF" in header
    assert "✅ OK" in header
    assert "2026-09-01" in header
