"""Рендер продуктов в TXT / PDF."""

from typing import Literal

from heliogeo.products.base import Product
from heliogeo.render.pdf import render_pdf
from heliogeo.render.txt import render_txt

Format = Literal["txt", "pdf"]


def render_product(
    product: Product,
    fmt: Format = "txt",
    output_path: str | None = None,
) -> bytes:
    """Отрендерить продукт в указанный формат.

    Args:
        product: Product с текстом.
        fmt: "txt" | "pdf".
        output_path: путь для сохранения. Если None — только вернуть bytes.

    Returns:
        Байты в указанном формате.

    Raises:
        ValueError: если формат неизвестен.
    """
    if fmt == "txt":
        return render_txt(product.text, output_path)
    if fmt == "pdf":
        return render_pdf(product.text, output_path, title=product.metadata.product_type)
    raise ValueError(f"Unknown format: {fmt!r} (expected 'txt' or 'pdf')")


__all__ = [
    "Format",
    "render_pdf",
    "render_product",
    "render_txt",
]
