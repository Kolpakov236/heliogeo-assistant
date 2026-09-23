"""Сборка UnifiedPayload из источников.

ЗАГЛУШКА: реализация — в Спринте 2 (после получения датасета).
Сейчас возвращает NotImplementedError, чтобы pipeline мог работать
на пустом payload.
"""

from typing import Any

from heliogeo.core.logging import get_logger

logger = get_logger(__name__)


def build_payload() -> Any:
    """Собрать UnifiedPayload. Реализация — в Спринте 2."""
    raise NotImplementedError("build_payload not implemented yet (waiting for dataset)")
