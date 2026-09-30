"""Рендер продукта в TXT."""

from pathlib import Path


def render_txt(text: str, output_path: str | Path | None = None) -> bytes:
    """Отрендерить продукт в TXT.

    Args:
        text: текст продукта.
        output_path: путь для сохранения. Если None — только вернуть bytes.

    Returns:
        Байты UTF-8.
    """
    data = text.encode("utf-8")

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    return data
