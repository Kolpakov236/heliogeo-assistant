"""HTTP-клиент к API 503.ifz.ru/swx."""

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger
from heliogeo.utils.validators import DATE_RE, WINDOW_RE

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://503.ifz.ru/swx"


class SwxClientError(Exception):
    """Ошибка клиента swx."""


class SwxClient:
    """HTTP-клиент к swx API.

    Args:
        base_url: базовый URL. По умолчанию — 503.ifz.ru/swx.
        timeout: таймаут запроса в секундах.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self._base_url = (
            base_url or getattr(settings, "swx_base_url", None) or _DEFAULT_BASE_URL
        )
        self._timeout = timeout

    def _format_date(self, query_time: datetime | None) -> str:
        """Формат даты: YYYY-MM-DD или 'now'."""
        if query_time is None:
            return "now"
        return query_time.strftime("%Y-%m-%d")

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET-запрос с retry."""
        url = f"{self._base_url}{path}"
        logger.debug("GET %s params=%s", url, params)

        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise SwxClientError(f"Unexpected response type: {type(data).__name__}")
            return data

    def get_snapshot(
        self,
        query_time: datetime | None = None,
        window: str | None = None,
        no_live: bool = False,
    ) -> dict[str, Any]:
        """Снимок данных космической погоды.

        Args:
            query_time: момент UTC. None → «сейчас».
            window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
            no_live: запретить подмешивание live-данных SWPC.

        Returns:
            dict с полями: query_time, window, active_regions, flares,
            cmes, particles, solar_wind, geomag, sources, notes.

        Raises:
            SwxClientError: при ошибке HTTP.
        """
        date_str = self._format_date(query_time)
        params: dict[str, Any] = {}
        if window and WINDOW_RE.fullmatch(window):
            params["window"] = window
        if no_live:
            params["no_live"] = "true"

        try:
            return self._get(f"/data/{date_str}", params=params)
        except httpx.HTTPError as e:
            logger.exception("Failed to get snapshot for %s", date_str)
            raise SwxClientError(f"Failed to get snapshot: {e}") from e

    def get_text(
        self,
        query_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Текст сводки (Forecast Discussion / RSGA).

        Args:
            query_time: момент UTC. None → «сейчас».

        Returns:
            dict с полями: query_time, product, kind, cycle, issued,
            sections, probabilities, text, source, available.

        Raises:
            SwxClientError: при ошибке HTTP.
        """
        date_str = self._format_date(query_time)

        try:
            return self._get(f"/text/{date_str}")
        except httpx.HTTPError as e:
            logger.exception("Failed to get text for %s", date_str)
            raise SwxClientError(f"Failed to get text: {e}") from e
