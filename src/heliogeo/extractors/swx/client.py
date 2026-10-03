"""HTTP-клиент к API 503.ifz.ru/swx."""

from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://503.ifz.ru/swx"

# Повторяем только то, что может пройти само: таймауты и сетевые сбои, перегрузка (408, 429) и
# ошибки сервера (5xx). Остальные 4xx повтор не лечит: 404 означает, что за момент нет данных.
_RETRY_STATUSES = frozenset({408, 429})


class SwxClientError(Exception):
    """Ошибка клиента swx."""


def _is_transient(exc: BaseException) -> bool:
    """Стоит ли повторить запрос после этой ошибки."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in _RETRY_STATUSES or status >= 500
    return isinstance(exc, httpx.TransportError)


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
        self._base_url = base_url or getattr(settings, "swx_base_url", None) or _DEFAULT_BASE_URL
        self._timeout = timeout

    def _format_time(self, query_time: datetime | None) -> str:
        """Момент в пути запроса: ``YYYY-MM-DDTHH:MM`` (UTC, с точностью до минуты) или ``now``.

        Время передаётся всегда: дата без времени трактуется источником как 12:00 UTC, и запрос
        на 00:30 молча превратился бы в запрос на полдень. Наивное время запрещено: оно сдвигает
        момент на часовой пояс машины.

        Raises:
            ValueError: если ``query_time`` без часового пояса.
        """
        if query_time is None:
            return "now"
        if query_time.tzinfo is None or query_time.utcoffset() is None:
            raise ValueError(f"query_time должен быть с часовым поясом, получено {query_time!r}")
        return query_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET-запрос; до трёх попыток при временных ошибках (см. ``_is_transient``)."""
        url = f"{self._base_url}{path}"
        logger.debug("GET %s params=%s", url, params)

        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise SwxClientError(f"Response is not valid JSON: {exc}") from exc
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
            query_time: момент UTC с часовым поясом. None → «сейчас».
            window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
            no_live: запретить подмешивание live-данных SWPC. Для воспроизводимых прогонов:
                только с датами старше двух суток.

        Returns:
            dict с полями: query_time, window, active_regions, flares,
            cmes, particles, solar_wind, geomag, sources, notes.

        Raises:
            SwxClientError: при ошибке HTTP или невалидном ответе.
            ValueError: если ``query_time`` без часового пояса.
        """
        moment = self._format_time(query_time)
        params: dict[str, Any] = {}
        if window:
            params["window"] = window
        if no_live:
            params["no_live"] = "true"

        try:
            return self._get(f"/data/{moment}", params=params)
        except httpx.HTTPError as e:
            logger.exception("Failed to get snapshot for %s", moment)
            raise SwxClientError(f"Failed to get snapshot: {e}") from e

    def get_text(
        self,
        query_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Текст сводки (Forecast Discussion / RSGA).

        Args:
            query_time: момент UTC с часовым поясом. None → «сейчас».

        Returns:
            dict с полями: query_time, product, kind, cycle, issued,
            sections, probabilities, text, source, available.

        Raises:
            SwxClientError: при ошибке HTTP или невалидном ответе.
            ValueError: если ``query_time`` без часового пояса.
        """
        moment = self._format_time(query_time)

        try:
            return self._get(f"/text/{moment}")
        except httpx.HTTPError as e:
            logger.exception("Failed to get text for %s", moment)
            raise SwxClientError(f"Failed to get text: {e}") from e
