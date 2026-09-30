"""Базовый интерфейс доставки."""

from abc import ABC, abstractmethod

from heliogeo.products.base import Product


class DeliveryError(Exception):
    """Ошибка доставки."""


class BaseDelivery(ABC):
    """Базовый класс доставки продукта."""

    @abstractmethod
    def send(
        self,
        product: Product,
        formats: list[str] | None = None,
    ) -> None:
        """Отправить продукт.

        Args:
            product: Product с текстом.
            formats: список форматов ("txt", "pdf"). None → ["txt"].

        Raises:
            DeliveryError: при ошибке доставки.
        """
        ...
