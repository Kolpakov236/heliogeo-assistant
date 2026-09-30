"""Сборка UnifiedPayload из источников данных.

Основной источник — API 503.ifz.ru/swx.
"""

from datetime import datetime
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.extractor import (
    SwxExtractor,
    SwxExtractorError,
)
from heliogeo.schema import UnifiedPayload

logger = get_logger(__name__)


class BuilderError(Exception):
    """Ошибка сборки payload."""


def build_payload(
    query_time: datetime | None = None,
    window: str | None = None,
    no_live: bool = False,
) -> UnifiedPayload:
    """Собрать UnifiedPayload из swx API.

    Args:
        query_time: момент UTC. None → «сейчас».
        window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
        no_live: запретить подмешивание live-данных SWPC.

    Returns:
        UnifiedPayload с данными.

    Raises:
        BuilderError: если extractor упал.
    """
    logger.info(
        "Building payload (query_time=%s, window=%s, no_live=%s)",
        query_time,
        window,
        no_live,
    )

    extractor = SwxExtractor()

    try:
        payload, _ = extractor.extract(
            query_time=query_time,
            window=window,
            no_live=no_live,
            include_text=False,  # текст не нужен для payload
        )
    except SwxExtractorError as e:
        logger.exception("Failed to extract payload")
        raise BuilderError(f"Failed to build payload: {e}") from e

    logger.info(
        "Payload built: issued=%s, %d AR, %d flares, Kp=%.2f",
        payload.metadata.issued_utc,
        len(payload.solar_activity.active_regions),
        len(payload.solar_activity.flares_last_24h),
        payload.geospace.kp_observed or 0.0,
    )

    return payload


def build_payload_dict(
    query_time: datetime | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Собрать payload как dict (для передачи в JSON).

    Удобно для сериализации и логирования.
    """
    payload = build_payload(query_time=query_time, **kwargs)
    return payload.model_dump(mode="json")
