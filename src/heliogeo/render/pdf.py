"""Рендер продукта в PDF через reportlab."""

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from heliogeo.core.logging import get_logger

logger = get_logger(__name__)


def render_pdf(
    text: str,
    output_path: str | Path | None = None,
    title: str = "Heliogeo Report",
) -> bytes:
    """Отрендерить продукт в PDF.

    Args:
        text: текст продукта (моноширинный).
        output_path: путь для сохранения. Если None — только вернуть bytes.
        title: заголовок PDF.

    Returns:
        Байты PDF.
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=title,
    )

    styles = getSampleStyleSheet()
    body_style = styles["Code"]
    header_style = styles["Heading1"]

    story = [
        Paragraph(title, header_style),
        Spacer(1, 6 * mm),
        Paragraph(
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            styles["Normal"],
        ),
        Spacer(1, 4 * mm),
    ]

    for line in text.splitlines():
        safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe_line or "&nbsp;", body_style))

    doc.build(story)
    data = buffer.getvalue()
    buffer.close()

    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    logger.info("PDF rendered: %d bytes", len(data))
    return data
