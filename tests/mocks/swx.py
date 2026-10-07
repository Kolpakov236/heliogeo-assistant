"""Моки источника SWX для тестов: фикстуры, готовые ответы клиента и подставной сервер.

Фикстуры в ``fixtures/`` — реальные ответы источника на 2026-09-01 (спокойный день) и 2024-05-11
(сильная буря). Тесты не ходят в сеть: ответы клиента собираются из этих файлов, а HTTP подменяется
транспортом ``httpx.MockTransport``.
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient
from heliogeo.extractors.swx.response import SwxDataResponse, SwxTextResponse

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

QUIET_DATA = "swx_snapshot_2026-09-01.json"
STORM_DATA = "swx_snapshot_2024-05-11.json"
QUIET_TEXT = "swx_text_2026-09-01.json"
STORM_TEXT = "swx_text_2024-05-11.json"

# Момент, на который источник ответил в обеих фикстурах: дата без времени — это 12:00 UTC.
QUIET_NOON = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
STORM_NOON = datetime(2024, 5, 11, 12, 0, tzinfo=UTC)
# Конец суток: ничего из окна ответа ещё не «из будущего».
QUIET_END_OF_DAY = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
STORM_END_OF_DAY = datetime(2024, 5, 11, 23, 59, tzinfo=UTC)

FETCHED_AT = datetime(2026, 10, 3, 12, 0, 5, tzinfo=UTC)


def load_fixture(name: str) -> dict[str, Any]:
    """Тело ответа из ``fixtures/`` как dict (каждый вызов — новая копия)."""
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def fixture_bytes(name: str) -> bytes:
    """Тело ответа из ``fixtures/`` как байты — так, как его отдал бы сервер."""
    return (FIXTURES / name).read_bytes()


def answered_for(raw: dict[str, Any], moment: datetime) -> dict[str, Any]:
    """Копия тела ответа, в которой источник «ответил» на ``moment`` (``query_time`` = ``moment``)."""
    body = copy.deepcopy(raw)
    body["query_time"] = moment.isoformat()
    return body


def make_data_response(
    raw: dict[str, Any] | None = None,
    *,
    at: datetime | None = None,
    window: str | None = None,
    no_live: bool = False,
    fetched_at: datetime = FETCHED_AT,
    follow_request: bool = False,
) -> SwxDataResponse:
    """Ответ ``/data`` на момент ``at`` (по умолчанию — полдень, на который ответил источник).

    Без ``raw`` берётся спокойный день 2026-09-01. Приведение к моменту запроса настоящее: на
    полдень в ``data`` остаются одна вспышка из восьми и один выброс из трёх; на конец суток
    (``QUIET_END_OF_DAY``) остаётся всё. Если ``at`` не совпадает с ``query_time`` фикстуры, в
    ``problems`` будет замечание «источник ответил на другой момент»; ``follow_request=True``
    моделирует источник, который ответил на запрошенный момент.
    """
    body = load_fixture(QUIET_DATA) if raw is None else raw
    moment = QUIET_NOON if at is None else at
    return SwxDataResponse.from_raw(
        answered_for(body, moment) if follow_request else body,
        requested_at=moment,
        window_spec=window,
        no_live=no_live,
        fetched_at=fetched_at,
    )


def make_text_response(
    raw: dict[str, Any] | None = None,
    *,
    at: datetime | None = None,
    no_live: bool = False,
    fetched_at: datetime = FETCHED_AT,
    follow_request: bool = False,
) -> SwxTextResponse:
    """Ответ ``/text`` на момент ``at``; без ``raw`` — выпуск 00:30 за 2026-09-01."""
    body = load_fixture(QUIET_TEXT) if raw is None else raw
    moment = QUIET_NOON if at is None else at
    return SwxTextResponse.from_raw(
        answered_for(body, moment) if follow_request else body,
        requested_at=moment,
        no_live=no_live,
        fetched_at=fetched_at,
    )


def json_response(body: Any, status: int = 200) -> httpx.Response:
    """HTTP-ответ с JSON-телом."""
    return httpx.Response(status, content=json.dumps(body).encode("utf-8"))


def fixture_response(name: str, status: int = 200) -> httpx.Response:
    """HTTP-ответ, тело которого — байты фикстуры."""
    return httpx.Response(status, content=fixture_bytes(name))


class FakeSwxServer:
    """Подставной сервер: ``httpx.Client`` клиента работает через ``MockTransport``.

    Ответы отдаются по очереди; последний повторяется, пока тест продолжает запросы. Элемент списка
    может быть исключением (например, ``httpx.ConnectTimeout``): оно будет поднято вместо ответа.
    Паузы между повторами клиента отключены. Все запросы лежат в ``requests``.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response | Exception]
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)
        real_client = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            reply = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
            if isinstance(reply, Exception):
                raise reply
            return reply

        def fake_client(**kwargs: Any) -> httpx.Client:
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("heliogeo.extractors.swx.client.httpx.Client", fake_client)
        monkeypatch.setattr(SwxClient._fetch.retry, "sleep", lambda seconds: None)  # type: ignore[attr-defined]
