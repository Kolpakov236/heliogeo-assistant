"""Продукты системы."""

from heliogeo.products.base import BaseProduct, Product, ProductMetadata
from heliogeo.products.icao_brief import ICAOBriefProduct
from heliogeo.products.swpc_discussion import SWPCDiscussionProduct

# Реестр продуктов по имени
PRODUCTS: dict[str, type[BaseProduct]] = {
    "icao_brief": ICAOBriefProduct,
    "swpc_discussion": SWPCDiscussionProduct,
}


def get_product(name: str) -> BaseProduct:
    """Получить продукт по имени.

    Args:
        name: icao_brief | swpc_discussion.

    Returns:
        Экземпляр продукта.

    Raises:
        KeyError: если продукт не найден.
    """
    if name not in PRODUCTS:
        raise KeyError(f"Unknown product: {name!r}. Available: {list(PRODUCTS)}")
    return PRODUCTS[name]()


__all__ = [
    "PRODUCTS",
    "BaseProduct",
    "ICAOBriefProduct",
    "Product",
    "ProductMetadata",
    "SWPCDiscussionProduct",
    "get_product",
]
