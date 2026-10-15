"""SwxExtractor: swx API → UnifiedPayload."""

from datetime import datetime
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.availability import AvailabilityReport
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
from heliogeo.extractors.swx.response import SwxDataResponse, SwxTextResponse
from heliogeo.schema import UnifiedPayload

logger = get_logger(__name__)


class SwxExtractorError(Exception):
    """Ошибка извлечения данных из swx."""


def payload_from_response(response: SwxDataResponse) -> UnifiedPayload:
    """UnifiedPayload из ответа ``/data``: берётся ``response.data``, приведённое к моменту запроса.

    Raises:
        SwxExtractorError: если собранный payload не проходит схему.
    """
    data = response.data
    snapshot_date = (response.query_time or response.requested_at).date().isoformat()
    solar_wind_source = (data.get("solar_wind") or {}).get("source", "unknown")

    payload_dict: dict[str, Any] = {
        "metadata": {
            "issued_utc": response.query_time,
            "valid_until_utc": response.window_end or response.query_time,
            "generator_version": "1.0",
        },
        "solar_activity": map_solar_activity(data),
        "energetic_particles": map_particles(data.get("particles", {})),
        "solar_wind": map_solar_wind(data.get("solar_wind", {})),
        "geospace": map_geospace(data.get("geomag", {})),
        "forecast": map_forecast(data.get("geomag", {})),
        "images": map_images(None),
        "technical_issues": map_technical_issues(
            notes=list(response.notes),
            solar_wind_source=solar_wind_source,
            snapshot_date=snapshot_date,
        ),
    }

    try:
        payload = UnifiedPayload.model_validate(payload_dict)
    except Exception as e:
        logger.exception("Payload validation failed")
        raise SwxExtractorError(f"Payload validation failed: {e}") from e
    return payload


class SwxExtractor:
    """Extractor для API 503.ifz.ru/swx.

    Собирает UnifiedPayload из ответов /data и /text. В payload идёт ``SwxDataResponse.data``:
    ответ, приведённый к моменту запроса, без событий и продуктов прогноза «из будущего».

    Args:
        client: HTTP-клиент. Если None — создаётся автоматически.

    Attributes:
        last_response: ответ ``/data`` последнего ``extract`` (``None`` до первого вызова).
    """

    def __init__(self, client: SwxClient | None = None) -> None:
        self._client = client or SwxClient()
        self.last_response: SwxDataResponse | None = None

    @property
    def last_availability(self) -> AvailabilityReport | None:
        """Что отброшено при последнем ``extract`` (``None`` до первого вызова)."""
        return None if self.last_response is None else self.last_response.availability

    def extract(
        self,
        query_time: datetime | None = None,
        window: str | None = None,
        no_live: bool = False,
        include_text: bool = False,
    ) -> tuple[UnifiedPayload, SwxTextResponse | None]:
        """Собрать UnifiedPayload из swx API.

        Args:
            query_time: момент UTC. None → «сейчас» по часам клиента (извлекатель системных часов
                не читает).
            window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
            no_live: запретить подмешивание live-данных SWPC.
            include_text: также вернуть /text (Forecast Discussion).

        Returns:
            Кортеж (payload, text_or_none).

        Raises:
            SwxExtractorError: если /data не удалось получить или
                валидация не прошла.
            ValueError: если ``query_time`` без часового пояса.
        """
        # Момент определяется один раз и одинаков для /data и /text.
        query_time = self._client.resolve_moment(query_time)

        logger.info("Swx extract for %s (window=%s)", query_time.isoformat(), window)

        # --- 1. Получить /data: ответ уже проверен и приведён к моменту запроса ---
        try:
            response = self._client.get_data(
                query_time=query_time,
                window=window,
                no_live=no_live,
            )
        except SwxClientError as e:
            logger.exception("Failed to fetch snapshot")
            raise SwxExtractorError(f"Failed to fetch snapshot: {e}") from e

        self.last_response = response
        logger.info("Availability: %s", response.availability.summary())

        # --- 2. Получить /text (опционально) ---
        text_response: SwxTextResponse | None = None
        if include_text:
            try:
                text_response = self._client.get_text(query_time=query_time, no_live=no_live)
            except SwxClientError as e:
                # Текст — не критичен, логируем и продолжаем
                logger.warning("Failed to fetch text (non-fatal): %s", e)

        payload = payload_from_response(response)

        logger.info(
            "Extracted payload: %d AR, %d flares, %d CME, Kp=%.1f",
            len(payload.solar_activity.active_regions),
            len(payload.solar_activity.flares_last_24h),
            len(payload.solar_activity.cme_list),
            payload.geospace.kp_observed or 0.0,
        )

        return payload, text_response
