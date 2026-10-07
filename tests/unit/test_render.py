"""Тесты рендера TXT и PDF."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heliogeo.products.base import Product, ProductMetadata
from heliogeo.render import render_product
from heliogeo.render.pdf import render_pdf
from heliogeo.render.txt import render_txt


def _product(text: str = "Kp 2.3. Dst -9 nT.") -> Product:
    return Product(
        metadata=ProductMetadata(
            product_type="icao_brief",
            issued_utc=datetime(2026, 9, 1, 12, tzinfo=UTC),
            grounding_ok=True,
        ),
        text=text,
    )


# --- render_txt ---


def test_render_txt_returns_bytes() -> None:
    """render_txt возвращает байты UTF-8."""
    data = render_txt("Hello, world!")
    assert isinstance(data, bytes)
    assert data == b"Hello, world!"


def test_render_txt_utf8_russian() -> None:
    """Кириллица кодируется в UTF-8."""
    data = render_txt("Привет, мир!")
    assert data.decode("utf-8") == "Привет, мир!"


def test_render_txt_writes_file(tmp_path: Path) -> None:
    """render_txt сохраняет файл."""
    path = tmp_path / "out.txt"
    render_txt("Hello", path)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "Hello"


def test_render_txt_creates_parent_dirs(tmp_path: Path) -> None:
    """render_txt создаёт родительские папки."""
    path = tmp_path / "deep" / "nested" / "out.txt"
    render_txt("Hello", path)
    assert path.exists()


# --- render_pdf ---


def test_render_pdf_returns_bytes() -> None:
    """render_pdf возвращает байты с PDF-сигнатурой."""
    data = render_pdf("Hello, world!", title="Test")
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF-")


def test_render_pdf_writes_file(tmp_path: Path) -> None:
    """render_pdf сохраняет файл."""
    path = tmp_path / "out.pdf"
    render_pdf("Hello", path, title="Test")
    assert path.exists()
    assert path.stat().st_size > 0


def test_render_pdf_empty_text() -> None:
    """render_pdf с пустым текстом не падает."""
    data = render_pdf("", title="Empty")
    assert data.startswith(b"%PDF-")


def test_render_pdf_multiline() -> None:
    """render_pdf с многострочным текстом."""
    text = "Line 1\nLine 2\nLine 3"
    data = render_pdf(text, title="Multi")
    assert data.startswith(b"%PDF-")


# --- render_product ---


def test_render_product_txt() -> None:
    """render_product(format='txt') → байты."""
    product = _product()
    data = render_product(product, fmt="txt")
    assert data == b"Kp 2.3. Dst -9 nT."


def test_render_product_pdf() -> None:
    """render_product(format='pdf') → PDF-байты."""
    product = _product()
    data = render_product(product, fmt="pdf")
    assert data.startswith(b"%PDF-")


def test_render_product_unknown_format() -> None:
    """render_product с неизвестным форматом → ValueError."""
    product = _product()
    with pytest.raises(ValueError, match="Unknown format"):
        render_product(product, fmt="docx")  # type: ignore[arg-type]


def test_render_product_txt_with_path(tmp_path: Path) -> None:
    """render_product сохраняет TXT."""
    product = _product()
    path = tmp_path / "out.txt"
    render_product(product, fmt="txt", output_path=str(path))
    assert path.read_text(encoding="utf-8") == product.text


def test_render_product_pdf_with_path(tmp_path: Path) -> None:
    """render_product сохраняет PDF."""
    product = _product()
    path = tmp_path / "out.pdf"
    render_product(product, fmt="pdf", output_path=str(path))
    assert path.exists()
    assert path.stat().st_size > 0


# --- Воспроизводимость PDF ---


def test_render_pdf_is_reproducible() -> None:
    """Одинаковые аргументы — одинаковые байты, сколько бы времени ни прошло между вызовами."""
    moment = datetime(2026, 9, 1, 12, tzinfo=UTC)
    first = render_pdf("Kp 2.3", title="T", issued_utc=moment)
    time.sleep(1.1)  # метка времени reportlab имеет точность до секунды
    assert render_pdf("Kp 2.3", title="T", issued_utc=moment) == first


def test_render_pdf_issue_moment_changes_the_document() -> None:
    first = render_pdf("Kp 2.3", title="T", issued_utc=datetime(2026, 9, 1, 12, tzinfo=UTC))
    second = render_pdf("Kp 2.3", title="T", issued_utc=datetime(2026, 9, 2, 12, tzinfo=UTC))
    assert first != second


def test_render_pdf_without_issue_moment_has_no_clock_line() -> None:
    """Без момента выпуска строка с датой не печатается, а часы не читаются."""
    assert render_pdf("Kp 2.3", title="T") == render_pdf("Kp 2.3", title="T")


def test_render_product_pdf_uses_the_issue_moment_of_the_product() -> None:
    product = _product()
    assert render_product(product, fmt="pdf") == render_pdf(
        product.text, title="icao_brief", issued_utc=product.metadata.issued_utc
    )
