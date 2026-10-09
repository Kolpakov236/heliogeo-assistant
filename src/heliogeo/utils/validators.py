"""
Регулярные выражения
"""

import re
from datetime import datetime

import typer

# query extractor re
WINDOW_RE = re.compile(r"(\d+)([mhd])")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate_date(value: str | None) -> datetime | None:
    """
    Валидация и парсинг даты
    """
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    if not DATE_RE.fullmatch(value):
        raise typer.BadParameter("Формат должен быть YYYY-MM-DD")

    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise typer.BadParameter("Указана несуществующая дата")


def validate_window(value: str | None) -> str | None:
    """
    Валидация окна агрегации
    """
    if value is None:
        return None

    value = value.strip()
    if not WINDOW_RE.fullmatch(value):
        raise typer.BadParameter("Формат: 15m, 1h, 1d")

    return value
