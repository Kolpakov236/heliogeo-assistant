"""Доставка продукта в Telegram."""

from typing import cast

import httpx

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger
from heliogeo.delivery.base import BaseDelivery, DeliveryError
from heliogeo.products.base import Product
from heliogeo.render import Format, render_product

logger = get_logger(__name__)


class TelegramDelivery(BaseDelivery):
    """Доставка продукта в Telegram-чат.

    Args:
        bot_token: токен бота. None → из Settings.
        chat_id: ID чата. None → из Settings.
        dry_run: если True — не отправлять, только логировать.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        settings = get_settings()
        self._bot_token = bot_token or settings.telegram_bot_token
        self._chat_id = chat_id or settings.telegram_chat_id
        self._dry_run = dry_run

        if not self._dry_run and not self._bot_token:
            raise DeliveryError("TELEGRAM_BOT_TOKEN is not set")
        if not self._dry_run and not self._chat_id:
            raise DeliveryError("TELEGRAM_CHAT_ID is not set")

    def send(
        self,
        product: Product,
        formats: list[str] | None = None,
    ) -> None:
        """Отправить продукт в Telegram.

        Отправляет:
        - Текстовое сообщение (header + text).
        - Затем файлы (txt, pdf) как документы.
        """
        formats = formats or ["txt"]
        header = self._build_header(product)

        if self._dry_run:
            logger.info(
                "[DRY RUN] Would send to chat %s: %s (%s)",
                self._chat_id,
                header,
                ", ".join(formats),
            )
            return

        try:
            self._send_sync(product, formats, header)
        except httpx.HTTPError as e:
            logger.exception("Telegram delivery failed")
            raise DeliveryError(f"Telegram delivery failed: {e}") from e

    def _build_header(self, product: Product) -> str:
        """Заголовок сообщения."""
        meta = product.metadata
        status = "✅ OK" if product.ok else "❌ GROUNDING FAILED"
        return (
            f"*{meta.product_type.upper()}*\n"
            f"Дата: `{meta.issued_utc.isoformat()}`\n"
            f"Статус: {status}\n"
            f"Grounding: `{meta.grounding_summary}`"
        )

    def _send_sync(
        self,
        product: Product,
        formats: list[str],
        header: str,
    ) -> None:
        """Синхронная отправка через httpx."""
        base_url = f"https://api.telegram.org/bot{self._bot_token}"

        with httpx.Client(timeout=30.0) as client:
            # 1. Текстовое сообщение с header + text
            message_text = f"{header}\n\n```\n{product.text[:3000]}\n```"
            resp = client.post(
                f"{base_url}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": message_text,
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()
            logger.info("Telegram message sent")

            # 2. Файлы
            for fmt in formats:
                data = render_product(product, fmt=cast(Format, fmt))
                filename = f"{product.metadata.product_type}_{fmt}.{fmt}"
                resp = client.post(
                    f"{base_url}/sendDocument",
                    data={"chat_id": self._chat_id},
                    files={"document": (filename, data)},
                )
                resp.raise_for_status()
                logger.info(
                    "Telegram file sent: %s (%d bytes)",
                    filename,
                    len(data),
                )
