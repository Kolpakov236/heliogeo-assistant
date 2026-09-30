"""Доставка продуктов."""

from typing import Any

from heliogeo.delivery.base import BaseDelivery, DeliveryError
from heliogeo.delivery.telegram import TelegramDelivery


def get_delivery(name: str = "telegram", **kwargs: Any) -> BaseDelivery:
    """Получить delivery по имени.

    Args:
        name: "telegram" | "mock".
        **kwargs: параметры для конструктора.

    Returns:
        Экземпляр BaseDelivery.

    Raises:
        ValueError: если имя неизвестно.
    """
    if name == "telegram":
        return TelegramDelivery(**kwargs)
    if name == "mock":
        return TelegramDelivery(dry_run=True)
    raise ValueError(f"Unknown delivery: {name!r}")


__all__ = [
    "BaseDelivery",
    "DeliveryError",
    "TelegramDelivery",
    "get_delivery",
]
