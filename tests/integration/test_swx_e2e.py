"""E2E тест: полный цикл от swx API до продукта.

Использует:
- Реальные fixture'ы из /swx/data (сохранены в fixtures/).
- Mock-LLM (не ходит в интернет).
- Реальные extractors, mapper, generator, grounding.

НЕ ходит в интернет.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from heliogeo.extractors.swx.client import SwxClient
from heliogeo.extractors.swx.extractor import SwxExtractor
from heliogeo.llm import MockLLMProvider, generate_product
from heliogeo.products import ICAOBriefProduct, SWPCDiscussionProduct
from heliogeo.schema import UnifiedPayload
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    QUIET_NOON,
    QUIET_TEXT,
    STORM_DATA,
    STORM_NOON,
    STORM_TEXT,
    FakeSwxServer,
    fixture_response,
    make_data_response,
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def snapshot_quiet() -> dict:
    """Спокойный день 2026-09-01."""
    path = FIXTURES_DIR / "swx_snapshot_2026-09-01.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot_storm() -> dict:
    """Бурный день 2024-05-11."""
    path = FIXTURES_DIR / "swx_snapshot_2024-05-11.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def client() -> SwxClient:
    """Mock-клиент (URL не используется, т.к. get_data мокается)."""
    return SwxClient(base_url="https://test.local")


# --- E2E: спокойный день ---


def test_e2e_quiet_extract_to_payload(client: SwxClient, snapshot_quiet: dict) -> None:
    """Шаг 1: extract → UnifiedPayload."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert isinstance(payload, UnifiedPayload)
    assert len(payload.solar_activity.active_regions) == 6
    assert payload.geospace.current_level == "quiet"
    assert payload.geospace.kp_observed is not None
    assert payload.geospace.kp_observed < 3


def test_e2e_quiet_payload_to_icao(client: SwxClient, snapshot_quiet: dict) -> None:
    """Шаг 2: payload → ICAO product (mock LLM)."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    provider = MockLLMProvider(response="No data available.")
    product = ICAOBriefProduct().generate(payload, provider)

    assert product.ok
    assert product.text == "No data available."
    assert product.metadata.product_type == "icao_brief"
    assert product.metadata.grounding_ok is True


def test_e2e_quiet_payload_to_swpc(client: SwxClient, snapshot_quiet: dict) -> None:
    """Шаг 3: payload → SWPC product (mock LLM)."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract()

    provider = MockLLMProvider(response="Solar activity is quiet.")
    product = SWPCDiscussionProduct().generate(payload, provider)

    assert product.ok
    assert product.text == "Solar activity is quiet."


def test_e2e_quiet_full_chain(client: SwxClient, snapshot_quiet: dict) -> None:
    """Полный цикл: extract → ICAO + SWPC + grounding."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    provider = MockLLMProvider(
        responses=[
            "Solar activity was at low levels. Kp 2.3. Dst -9 nT.",
            "Solar activity is quiet. No Earth-directed CMEs.",
        ]
    )

    icao = ICAOBriefProduct().generate(payload, provider)
    swpc = SWPCDiscussionProduct().generate(payload, provider)

    assert icao.ok
    assert swpc.ok
    assert provider._call_count == 2


# --- E2E: бурный день ---


def test_e2e_storm_full_chain(client: SwxClient, snapshot_storm: dict) -> None:
    """Бурный день: extract → products."""
    extractor = SwxExtractor(client=client)
    with patch.object(
        client, "get_data", return_value=make_data_response(snapshot_storm, at=STORM_NOON)
    ):
        payload, _ = extractor.extract(query_time=datetime(2024, 5, 11, 12, tzinfo=UTC))

    assert payload.geospace.current_level == "storm"
    assert payload.geospace.kp_observed is not None
    assert payload.geospace.kp_observed >= 5

    provider = MockLLMProvider(
        responses=[
            "Severe storm. Kp 9.0. Dst -500 nT.",  # галлюцинация
            "Geomagnetic storm conditions observed.",
        ]
    )

    ICAOBriefProduct().generate(payload, provider)
    swpc = SWPCDiscussionProduct().generate(payload, provider)

    # ICAO — галлюцинация (Kp 9.0, Dst -500 могут не совпасть)
    # SWPC — без чисел, ok
    assert swpc.ok


def test_e2e_storm_grounding_catches_hallucination(client: SwxClient, snapshot_storm: dict) -> None:
    """Grounding ловит выдуманные числа в бурном дне."""
    extractor = SwxExtractor(client=client)
    with patch.object(
        client, "get_data", return_value=make_data_response(snapshot_storm, at=STORM_NOON)
    ):
        payload, _ = extractor.extract(query_time=datetime(2024, 5, 11, 12, tzinfo=UTC))

    # Придумываем число, которого точно нет
    provider = MockLLMProvider(response="Kp observed: 99.9. Dst: -999 nT.")
    product = ICAOBriefProduct().generate(payload, provider)

    assert not product.ok
    assert "FAIL" in product.metadata.grounding_summary


# --- E2E: технические проблемы ---


def test_e2e_technical_issues_present(client: SwxClient, snapshot_quiet: dict) -> None:
    """technical_issues заполнены (OMNI + SGPS)."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract()

    assert payload.technical_issues is not None
    assert "omni_hro_1min" in payload.technical_issues
    assert "SGPS" in payload.technical_issues


# --- E2E: сериализация ---


def test_e2e_payload_roundtrip(client: SwxClient, snapshot_quiet: dict) -> None:
    """Payload сериализуется в JSON и обратно."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract()

    json_str = payload.model_dump_json()
    restored = UnifiedPayload.model_validate_json(json_str)

    assert restored.geospace.kp_observed == payload.geospace.kp_observed
    assert len(restored.solar_activity.active_regions) == len(payload.solar_activity.active_regions)


# --- E2E: generate_product напрямую ---


def test_e2e_generate_product_directly(client: SwxClient, snapshot_quiet: dict) -> None:
    """generate_product работает с реальным payload."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(snapshot_quiet)):
        payload, _ = extractor.extract()

    provider = MockLLMProvider(response="No data available.")
    result = generate_product("icao_brief", payload, provider)

    assert result.ok
    assert result.product == "icao_brief"
    assert result.payload_json != ""
    assert "metadata" in result.payload_json


# --- E2E: через HTTP-слой (подставной сервер вместо сети) ---


@pytest.mark.parametrize(
    ("data_name", "text_name", "moment", "expected_name"),
    [
        pytest.param(QUIET_DATA, QUIET_TEXT, QUIET_NOON, "payload_quiet_1200.json", id="quiet"),
        pytest.param(STORM_DATA, STORM_TEXT, STORM_NOON, "payload_storm_1200.json", id="storm"),
    ],
)
def test_e2e_through_the_http_layer(
    monkeypatch: pytest.MonkeyPatch,
    data_name: str,
    text_name: str,
    moment: datetime,
    expected_name: str,
) -> None:
    """Байты ответа -> клиент -> типизированный ответ -> экстрактор -> тот же payload, что раньше."""
    server = FakeSwxServer(monkeypatch, [fixture_response(data_name), fixture_response(text_name)])
    extractor = SwxExtractor(
        client=SwxClient(base_url="https://test.local", clock=lambda: FETCHED_AT)
    )

    payload, text = extractor.extract(query_time=moment, include_text=True)

    stamp = moment.strftime("%Y-%m-%dT%H:%M")
    assert [request.url.path for request in server.requests] == [
        f"/data/{stamp}",
        f"/text/{stamp}",
    ]
    expected = json.loads((FIXTURES_DIR / "expected" / expected_name).read_text(encoding="utf-8"))
    assert payload.model_dump(mode="json") == expected
    assert extractor.last_response is not None
    assert extractor.last_response.problems == ()
    assert extractor.last_response.unknown_fields == ()
    assert text is not None
    assert text.kind == "discussion"
    assert text.problems == ()
    assert text.available


def test_e2e_http_failure_of_text_does_not_break_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Текст не критичен: 404 на /text при исправном /data — payload собирается."""
    FakeSwxServer(monkeypatch, [fixture_response(QUIET_DATA), httpx.Response(404)])
    extractor = SwxExtractor(client=SwxClient(base_url="https://test.local"))

    payload, text = extractor.extract(query_time=QUIET_NOON, include_text=True)

    assert isinstance(payload, UnifiedPayload)
    assert text is None
