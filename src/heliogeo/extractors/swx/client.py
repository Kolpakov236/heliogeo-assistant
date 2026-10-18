"""HTTP-клиент к API 503.ifz.ru/swx."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.response import (
    SwxDataResponse,
    SwxResponseError,
    SwxTextResponse,
)
from heliogeo.mirror.raw import RawRecord
from heliogeo.utils.validators import WINDOW_RE

logger = get_logger(__name__)

_DEFAULT_BASE_URL = "https://503.ifz.ru/swx"

# Повторяем только то, что может пройти само: таймауты и сетевые сбои, перегрузка (408, 429) и
# ошибки сервера (5xx). Остальные 4xx повтор не лечит: 404 означает, что за момент нет данных.
_RETRY_STATUSES = frozenset({408, 429})


SwxErrorKind = Literal["unavailable", "not_found", "rejected", "malformed"]


class RawSink(Protocol):
    """Куда клиент сохраняет тело успешного ответа как получено (``RawStore.save_snapshot``)."""

    def __call__(
        self,
        *,
        endpoint: Literal["data", "text"],
        body: bytes,
        requested_at: datetime,
        window: str | None,
        no_live: bool,
        fetched_at: datetime,
    ) -> RawRecord: ...


class SwxClientError(Exception):
    """Ошибка клиента swx.

    Attributes:
        kind: что случилось. ``unavailable`` — сеть, таймаут, HTTP 408/429/5xx: повтор может
            помочь. ``not_found`` — HTTP 404, за этот момент данных нет. ``rejected`` — источник
            отверг запрос (другие 4xx: адрес, параметры, доступ). ``malformed`` — ответ получен,
            но это не то, что ждали (не JSON, не объект, не похоже на ответ эндпоинта).
    """

    def __init__(self, message: str, *, kind: SwxErrorKind = "malformed") -> None:
        super().__init__(message)
        self.kind = kind


def _is_transient(exc: BaseException) -> bool:
    """Стоит ли повторить запрос после этой ошибки."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in _RETRY_STATUSES or status >= 500
    return isinstance(exc, httpx.TransportError)


def _error_kind(exc: httpx.HTTPError) -> SwxErrorKind:
    """Что значит ошибка HTTP для вызывающего: повтор, нет данных или отказ источника."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return "not_found"
        return "unavailable" if _is_transient(exc) else "rejected"
    return "unavailable" if isinstance(exc, httpx.TransportError) else "malformed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SwxClient:
    """HTTP-клиент к swx API.

    Методы возвращают типизированные ответы (``SwxDataResponse``, ``SwxTextResponse``): форма,
    времена и неизвестные поля уже проверены, данные приведены к запрошенному моменту.

    Args:
        base_url: базовый URL. По умолчанию — 503.ifz.ru/swx.
        timeout: таймаут запроса в секундах.
        clock: источник текущего времени (UTC); подменяется в тестах. Нужен для запроса «сейчас» и
            для ``fetched_at`` ответа.
        raw_sink: куда сохранять тело каждого успешного ответа. Сохраняются только ответы,
            прошедшие проверку формы (не страница ошибки); в ответе заполняются ``raw_key`` и
            ``raw_sha256``. None — ответы не сохраняются.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        *,
        clock: Callable[[], datetime] = _utc_now,
        raw_sink: RawSink | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (
            base_url or getattr(settings, "swx_base_url", None) or _DEFAULT_BASE_URL
        )
        self._timeout = timeout
        self._clock = clock
        self._raw_sink = raw_sink
        # О неизвестном поле ответа предупреждаем один раз за жизнь клиента, а не при каждом запросе.
        self._reported_unknown: set[tuple[str, str]] = set()

    def resolve_moment(self, query_time: datetime | None) -> datetime:
        """Запрошенный момент: UTC, с точностью до минуты. ``None`` — «сейчас» по часам клиента.

        Источнику всегда уходит конкретное время, а не слово ``now``: так запрошенный момент один и
        тот же в пути запроса, в отсечке событий и в ответе. Часы клиента — единственное место, где
        «сейчас» определяется (извлекатель и пайплайн системных часов не читают), поэтому момент,
        определённый здесь, можно передавать дальше как событие прогона.

        Raises:
            ValueError: если ``query_time`` без часового пояса.
        """
        moment = self._clock() if query_time is None else query_time
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"query_time должен быть с часовым поясом, получено {moment!r}")
        return moment.astimezone(UTC).replace(second=0, microsecond=0)

    def _format_time(self, query_time: datetime) -> str:
        """Момент в пути запроса: ``YYYY-MM-DDTHH:MM`` (UTC, с точностью до минуты).

        Время передаётся всегда: дата без времени трактуется источником как 12:00 UTC, и запрос
        на 00:30 молча превратился бы в запрос на полдень. Наивное время запрещено: оно сдвигает
        момент на часовой пояс машины.

        Raises:
            ValueError: если ``query_time`` без часового пояса.
        """
        if query_time.tzinfo is None or query_time.utcoffset() is None:
            raise ValueError(f"query_time должен быть с часовым поясом, получено {query_time!r}")
        return query_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _fetch(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """GET-запрос; тело ответа как получено. До трёх попыток при временных ошибках."""
        url = f"{self._base_url}{path}"
        logger.debug("GET %s params=%s", url, params)

        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.content

    def _download(
        self, path: str, params: dict[str, Any] | None, what: str
    ) -> tuple[bytes, datetime]:
        """Тело ответа и время его получения; ошибки HTTP становятся ``SwxClientError``."""
        try:
            body = self._fetch(path, params)
        except httpx.HTTPError as e:
            logger.exception("Failed to get %s for %s", what, path)
            raise SwxClientError(f"Failed to get {what}: {e}", kind=_error_kind(e)) from e
        return body, self._clock()

    @staticmethod
    def _decode_object(body: bytes) -> dict[str, Any]:
        """JSON-объект из тела ответа; всё остальное — ошибка клиента."""
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise SwxClientError(f"Response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SwxClientError(f"Unexpected response type: {type(data).__name__}")
        return data

    def _report(self, response: SwxDataResponse | SwxTextResponse, endpoint: str) -> None:
        """Предупредить о том, что в ответе показалось неправильным; о новых полях — один раз."""
        for problem in response.problems:
            logger.warning(
                "SWX %s response for %s: %s", endpoint, response.requested_at.isoformat(), problem
            )
        new_fields = [
            name
            for name in response.unknown_fields
            if (endpoint, name) not in self._reported_unknown
        ]
        if new_fields:
            self._reported_unknown.update((endpoint, name) for name in new_fields)
            logger.warning(
                "SWX %s response has unknown fields: %s", endpoint, ", ".join(new_fields)
            )

    def get_data(
        self,
        query_time: datetime | None = None,
        window: str | None = None,
        no_live: bool = False,
    ) -> SwxDataResponse:
        """Данные космической погоды на момент ``query_time``.

        Args:
            query_time: момент UTC с часовым поясом (секунды отбрасываются). None → «сейчас».
            window: окно агрегации (15m, 1h, 1d). None → сутки UTC.
            no_live: запретить подмешивание live-данных SWPC. Для воспроизводимых прогонов:
                только с датами старше двух суток.

        Returns:
            Типизированный ответ: ``raw`` как получено, ``data`` приведён к запрошенному
            моменту (события и прогнозы «из будущего» убраны), плюс проверки и происхождение.

        Raises:
            SwxClientError: при ошибке HTTP, не-JSON, а также если тело не похоже на ответ
                ``/data`` (например, JSON с ошибкой вместо данных).
            ValueError: если ``query_time`` без часового пояса.
        """
        requested_at = self.resolve_moment(query_time)
        params: dict[str, Any] = {}
        if window and WINDOW_RE.fullmatch(window):
            params["window"] = window
        if no_live:
            params["no_live"] = "true"

        body, fetched_at = self._download(
            f"/data/{self._format_time(requested_at)}", params, what="snapshot"
        )
        raw = self._decode_object(body)
        try:
            response = SwxDataResponse.from_raw(
                raw,
                requested_at=requested_at,
                fetched_at=fetched_at,
                window_spec=window,
                no_live=no_live,
            )
        except SwxResponseError as exc:
            raise SwxClientError(f"Invalid /data response: {exc}") from exc
        if self._raw_sink is not None:
            record = self._raw_sink(
                endpoint="data",
                body=body,
                requested_at=requested_at,
                window=window,
                no_live=no_live,
                fetched_at=fetched_at,
            )
            response = replace(response, raw_sha256=record.entry.sha256, raw_key=record.entry.key)
        self._report(response, "/data")
        return response

    def get_text(
        self,
        query_time: datetime | None = None,
        no_live: bool = False,
    ) -> SwxTextResponse:
        """Текст сводки (Forecast Discussion / RSGA): последний выпуск не позже ``query_time``.

        Args:
            query_time: момент UTC с часовым поясом (секунды отбрасываются). None → «сейчас».
            no_live: запретить подмешивание live-данных SWPC.

        Returns:
            Типизированный ответ: выпуск, его разделы и список доступных выпусков ``available``.

        Raises:
            SwxClientError: при ошибке HTTP, не-JSON, а также если тело не похоже на ответ
                ``/text``.
            ValueError: если ``query_time`` без часового пояса.
        """
        requested_at = self.resolve_moment(query_time)
        params = {"no_live": "true"} if no_live else {}

        body, fetched_at = self._download(
            f"/text/{self._format_time(requested_at)}", params, what="text"
        )
        raw = self._decode_object(body)
        try:
            response = SwxTextResponse.from_raw(
                raw, requested_at=requested_at, fetched_at=fetched_at, no_live=no_live
            )
        except SwxResponseError as exc:
            raise SwxClientError(f"Invalid /text response: {exc}") from exc
        if self._raw_sink is not None:
            record = self._raw_sink(
                endpoint="text",
                body=body,
                requested_at=requested_at,
                window=None,
                no_live=no_live,
                fetched_at=fetched_at,
            )
            response = replace(response, raw_sha256=record.entry.sha256, raw_key=record.entry.key)
        self._report(response, "/text")
        return response
