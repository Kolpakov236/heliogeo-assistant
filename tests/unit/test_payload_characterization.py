"""Характеризационный тест: payload по реальным снимкам не меняется при рефакторинге клиента.

Эталоны ``fixtures/expected/payload_*.json`` сняты со старого кода (клиент возвращал dict, а
фильтр «из будущего» жил в экстракторе) перед переходом на типизированные ответы. Тест доказывает,
что новая цепочка даёт тот же payload: клиент -> типизированный ответ -> экстрактор -> маппер.

Если маппер или схема payload изменены намеренно, эталоны нужно пересоздать:

    REGENERATE_EXPECTED=1 poetry run pytest tests/unit/test_payload_characterization.py

и проверить дифф эталонов глазами: он и есть описание того, что изменилось в payload.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from heliogeo.extractors.swx.client import SwxClient
from heliogeo.extractors.swx.extractor import SwxExtractor
from tests.mocks.swx import (
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_NOON,
    STORM_DATA,
    STORM_END_OF_DAY,
    STORM_NOON,
    load_fixture,
    make_data_response,
)

EXPECTED_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "expected"

# (метка эталона, фикстура, момент запроса, вспышек и выбросов в payload)
CASES = [
    pytest.param("quiet_1200", QUIET_DATA, QUIET_NOON, (1, 1), id="quiet-noon"),
    pytest.param("quiet_2359", QUIET_DATA, QUIET_END_OF_DAY, (8, 3), id="quiet-end-of-day"),
    pytest.param("storm_1200", STORM_DATA, STORM_NOON, (4, 3), id="storm-noon"),
    pytest.param("storm_2359", STORM_DATA, STORM_END_OF_DAY, (11, 5), id="storm-end-of-day"),
]


def _build_payload(fixture: str, moment: datetime) -> dict[str, Any]:
    client = SwxClient(base_url="https://test.local")
    extractor = SwxExtractor(client=client)
    # условия снятия эталонов: тело ответа — как в фикстуре (query_time = полдень), момент запроса
    # задаёт отсечку; поэтому issued_utc во всех случаях равен полудню
    response = make_data_response(load_fixture(fixture), at=moment)
    with patch.object(client, "get_data", return_value=response):
        payload, _ = extractor.extract(query_time=moment)
    dumped: dict[str, Any] = payload.model_dump(mode="json")
    return dumped


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize(("tag", "fixture", "moment", "counts"), CASES)
def test_payload_is_unchanged(
    tag: str, fixture: str, moment: datetime, counts: tuple[int, int]
) -> None:
    path = EXPECTED_DIR / f"payload_{tag}.json"
    actual = _build_payload(fixture, moment)

    if os.environ.get("REGENERATE_EXPECTED") == "1":
        path.write_text(_serialize(actual), encoding="utf-8", newline="\n")
        pytest.skip(f"эталон {path.name} пересоздан")

    assert actual == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("tag", "fixture", "moment", "counts"), CASES)
def test_expected_payloads_are_not_degenerate(
    tag: str, fixture: str, moment: datetime, counts: tuple[int, int]
) -> None:
    """Эталоны отличаются друг от друга ровно тем, чем должны: фильтром по моменту запроса."""
    expected = json.loads((EXPECTED_DIR / f"payload_{tag}.json").read_text(encoding="utf-8"))

    solar_activity = expected["solar_activity"]
    assert (len(solar_activity["flares_last_24h"]), len(solar_activity["cme_list"])) == counts
    assert expected["solar_wind"]["density"] is not None  # среднее за окно, а не «последнее»
