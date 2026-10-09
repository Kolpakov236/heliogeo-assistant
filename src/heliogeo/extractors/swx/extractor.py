"""SwxExtractor: swx API → UnifiedPayload."""

from datetime import UTC, datetime
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.mapper import (
    map_forecast,
    map_geospace,
    map_images,
    map_particles,
    map_solar_activity,
    map_solar_wind,
    map_technical_issues,
)
from heliogeo.schema import UnifiedPayload

logger = get_logger(__name__)


class SwxExtractorError(Exception):
    """Ошибка извлечения данных из swx."""


class SwxExtractor:
    """Extractor для API 503.ifz.ru/swx.

    Собирает UnifiedPayload из ответов /data и /text.

    Args:
        client: HTTP-клиент. Если None — создаётся автоматически.
    """

    def __init__(self, client: SwxClient | None = None) -> None:
        self._client = client or SwxClient()

    def extract(
        self,
        query_time: datetime | None = None,
        window: str | None = None,
        no_live: bool = False,
        include_text: bool = False,
    ) -> tuple[UnifiedPayload, dict[str, Any] | None]:
        """Собрать UnifiedPayload из swx API.

        Args:
            query_time: момент UTC. None → «сейчас».
            window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
            no_live: запретить подмешивание live-данных SWPC.
            include_text: также вернуть /text (Forecast Discussion).

        Returns:
            Кортеж (payload, text_or_none).

        Raises:
            SwxExtractorError: если /data не удалось получить или
                валидация не прошла.
        """
        if query_time is None:
            query_time = datetime.now(UTC)

        logger.info("Swx extract for %s (window=%s)", query_time.isoformat(), window)

        # --- 1. Получить /data ---
        try:
            data = self._client.get_snapshot(
                query_time=query_time,
                window=window,
                no_live=no_live,
            )
        except SwxClientError as e:
            logger.exception("Failed to fetch snapshot")
            raise SwxExtractorError(f"Failed to fetch snapshot: {e}") from e

        # --- 2. Получить /text (опционально) ---
        text_data: dict[str, Any] | None = None
        if include_text:
            try:
                text_data = self._client.get_text(query_time=query_time)
            except SwxClientError as e:
                # Текст — не критичен, логируем и продолжаем
                logger.warning("Failed to fetch text (non-fatal): %s", e)

        # --- 3. Собрать dict ---
        snapshot_date = data.get("query_time", "")[:10] or "1970-01-01"
        solar_wind_source = (data.get("solar_wind") or {}).get("source", "unknown")
        source_products = {s["product"] for s in data.get("sources", [])}
        solar_wind = data.get("solar_wind") or {}

        payload_dict: dict[str, Any] = {
            "metadata": {
                "issued_utc": data.get("query_time"),
                "valid_until_utc": _window_end(data) or data.get("query_time"),
                "generator_version": "1.0",
                "source_products": source_products,
            },
            "solar_activity": map_solar_activity(data),
            "energetic_particles": map_particles(data.get("particles", {})),
            "solar_wind": map_solar_wind(solar_wind),
            "geospace": map_geospace(data.get("geomag", {})),
            "forecast": map_forecast(data.get("geomag", {})),
            "images": map_images(None),
            "technical_issues": map_technical_issues(
                notes=data.get("notes", []) or [],
                solar_wind_source=solar_wind_source,
                snapshot_date=snapshot_date,
                has_l1_rtsw=(
                    solar_wind_source == "rtsw" and bool(solar_wind.get("n_minutes"))
                ),
                omni_min_downloaded="omni_min" in source_products,
                forecast_available=("three_day_forecast" in source_products),
                discussion_available=("discussion" in source_products),
                donki_lag_days=None,
                dst_kind=None,
                active_regions_growth=next(
                    (
                        r["growth"]["status"]
                        for r in data.get("active_regions", [])
                        if r.get("growth", {}).get("status")
                    ),
                    None,
                ),
            ),
        }

        # --- 4. Валидация ---
        try:
            payload = UnifiedPayload.model_validate(payload_dict)
        except Exception as e:
            logger.exception("Payload validation failed")
            raise SwxExtractorError(f"Payload validation failed: {e}") from e

        logger.info(
            "Extracted payload: %d AR, %d flares, %d CME, Kp=%.1f",
            len(payload.solar_activity.active_regions),
            len(payload.solar_activity.flares_last_24h),
            len(payload.solar_activity.cme_list),
            payload.geospace.kp_observed or 0.0,
        )

        return payload, text_data


def _window_end(data: dict[str, Any]) -> str | None:
    """Достать end из window."""
    window = data.get("window")
    if isinstance(window, dict):
        return window.get("end")
    return None
