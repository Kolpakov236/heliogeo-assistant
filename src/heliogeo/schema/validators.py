"""Переиспользуемые валидаторы для схем."""

import re
from datetime import datetime

# Класс вспышки: A/B/C/M/X + обязательное число (M1.5, X10, C1)
_FLARE_RE = re.compile(r"^[ABCMX]\d+(\.\d+)?$")


def valid_utc(value: str | datetime) -> str | datetime:
    """Проверить, что значение — корректное UTC-время."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"UTC time must be str or datetime, got {type(value).__name__}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"Invalid UTC time: {value}") from e
    return value


def valid_kp(value: float | None) -> float | None:
    """Kp должен быть от 0 до 9."""
    if value is None:
        return value
    if not 0 <= value <= 9:
        raise ValueError(f"Kp must be between 0 and 9, got {value}")
    return value


def valid_dst(value: float | None) -> float | None:
    """Dst должен быть в разумных границах (-500 до +50 нТл)."""
    if value is None:
        return value
    if not -500 <= value <= 50:
        raise ValueError(f"Dst out of range [-500, 50], got {value}")
    return value


def valid_flare_class(value: str) -> str:
    """Проверить класс вспышки: A/B/C/M/X + обязательное число.

    Примеры: A1, B5.5, C1, M1.5, X10, X20.5.
    """
    if not _FLARE_RE.match(value):
        raise ValueError(f"Invalid flare class: {value!r} (expected A/B/C/M/X with number)")
    return value
