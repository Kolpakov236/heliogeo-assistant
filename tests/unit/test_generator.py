"""Тесты генератора продуктов через LLM."""

from datetime import UTC, datetime

import pytest

from heliogeo.llm import (
    LLMError,
    MockLLMProvider,
    generate_product,
    generate_products,
)
from heliogeo.schema import Metadata, UnifiedPayload


def _payload() -> UnifiedPayload:
    now = datetime(2026, 5, 12, tzinfo=UTC)
    return UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )


def test_generate_product_ok() -> None:
    """Mock возвращает текст без галлюцинаций → ok."""
    provider = MockLLMProvider(response="No data available.")
    result = generate_product("icao_brief", _payload(), provider)
    assert result.ok
    assert result.product == "icao_brief"
    assert result.text == "No data available."
    assert result.provider_name == "MockLLMProvider"
    assert result.duration_ms >= 0


def test_generate_product_grounding_fail() -> None:
    """Mock возвращает выдуманное число → grounding fail, но не бросает."""
    provider = MockLLMProvider(response="Kp observed: 99.9")
    result = generate_product("icao_brief", _payload(), provider)
    assert not result.ok
    assert any(i.value == "99.9" for i in result.grounding.issues)


def test_generate_product_fail_on_grounding() -> None:
    """fail_on_grounding=True → бросает LLMError."""
    provider = MockLLMProvider(response="Kp observed: 99.9")
    with pytest.raises(LLMError, match="Grounding failed") as caught:
        generate_product(
            "icao_brief",
            _payload(),
            provider,
            fail_on_grounding=True,
        )
    assert caught.value.kind == "validation"


def test_generate_product_provider_error() -> None:
    """Ошибка провайдера → LLMError пробрасывается."""
    provider = MockLLMProvider(response="ok", fail_on_call=1)
    with pytest.raises(LLMError, match="Mock failure"):
        generate_product("icao_brief", _payload(), provider)


def test_generate_product_records_calls() -> None:
    """Провайдер получает system + prompt."""
    provider = MockLLMProvider(response="ok")
    generate_product("icao_brief", _payload(), provider)
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["system"] is not None
    assert "гелиогеофизик" in call["system"].lower()
    assert "ICAO" in call["prompt"]


def test_generate_products_multiple() -> None:
    """Генерация нескольких продуктов подряд."""
    provider = MockLLMProvider(responses=["ICAO text", "SWPC text"])
    results = generate_products(
        ["icao_brief", "swpc_discussion"],
        _payload(),
        provider,
    )
    assert len(results) == 2
    assert results[0].product == "icao_brief"
    assert results[1].product == "swpc_discussion"
    assert results[0].text == "ICAO text"
    assert results[1].text == "SWPC text"


def test_generate_product_payload_json_set() -> None:
    """payload_json не пустой для Pydantic-модели."""
    provider = MockLLMProvider(response="ok")
    result = generate_product("icao_brief", _payload(), provider)
    assert result.payload_json != ""
    assert "metadata" in result.payload_json


def test_generate_product_with_dict() -> None:
    """Работает и с dict вместо Pydantic-модели."""
    provider = MockLLMProvider(response="ok")
    data = {
        "metadata": {
            "issued_utc": "2026-05-12T00:00:00Z",
            "valid_until_utc": "2026-05-13T00:00:00Z",
            "generator_version": "1.0",
        }
    }
    result = generate_product("icao_brief", data, provider)
    assert result.ok
    assert "metadata" in result.payload_json


# --- Момент выпуска берётся из payload, а не из системных часов ---


def test_generation_result_takes_the_issue_moment_from_the_payload_model() -> None:
    result = generate_product("icao_brief", _payload(), MockLLMProvider(response="ok"))
    assert result.issued_utc == datetime(2026, 5, 12, tzinfo=UTC)


def test_generation_result_takes_the_issue_moment_from_a_dict_payload() -> None:
    data = {"metadata": {"issued_utc": "2026-05-12T06:30:00Z"}}
    result = generate_product("icao_brief", data, MockLLMProvider(response="ok"))
    assert result.issued_utc == datetime(2026, 5, 12, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"metadata": None},
        {"metadata": {}},
        {"metadata": {"issued_utc": "вчера"}},
        {"metadata": {"issued_utc": "2026-05-12T06:30:00"}},  # без пояса момент неоднозначен
        {"metadata": {"issued_utc": 12345}},
    ],
)
def test_generation_result_has_no_issue_moment_when_the_payload_does_not_give_one(
    payload: dict,
) -> None:
    result = generate_product("icao_brief", payload, MockLLMProvider(response="ok"))
    assert result.issued_utc is None
