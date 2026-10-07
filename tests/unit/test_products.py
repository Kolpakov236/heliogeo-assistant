"""Тесты продуктов."""

from datetime import UTC, datetime

import pytest

from heliogeo.llm import MockLLMProvider
from heliogeo.products import (
    PRODUCTS,
    ICAOBriefProduct,
    Product,
    SWPCDiscussionProduct,
    get_product,
)

PAYLOAD = {
    "metadata": {
        "issued_utc": "2026-05-12T00:00:00Z",
        "valid_until_utc": "2026-05-13T12:00:00Z",
        "generator_version": "1.0",
    }
}


def test_get_product_icao() -> None:
    """get_product('icao_brief') возвращает ICAOBriefProduct."""
    p = get_product("icao_brief")
    assert isinstance(p, ICAOBriefProduct)


def test_get_product_swpc() -> None:
    """get_product('swpc_discussion') возвращает SWPCDiscussionProduct."""
    p = get_product("swpc_discussion")
    assert isinstance(p, SWPCDiscussionProduct)


def test_get_product_unknown() -> None:
    """get_product('unknown') → KeyError."""
    with pytest.raises(KeyError, match="Unknown product"):
        get_product("unknown")


def test_products_registry() -> None:
    """Реестр содержит icao_brief и swpc_discussion."""
    assert "icao_brief" in PRODUCTS
    assert "swpc_discussion" in PRODUCTS


def test_icao_product_ok() -> None:
    """ICAO product с чистым текстом → ok."""
    provider = MockLLMProvider(response="No data available.")
    product = ICAOBriefProduct().generate(PAYLOAD, provider)

    assert isinstance(product, Product)
    assert product.ok
    assert product.text == "No data available."
    assert product.language == "en"
    assert product.metadata.product_type == "icao_brief"
    assert product.metadata.provider_name == "MockLLMProvider"


def test_icao_product_grounding_fail() -> None:
    """ICAO product с галлюцинацией → not ok."""
    provider = MockLLMProvider(response="Kp observed: 99.9")
    product = ICAOBriefProduct().generate(PAYLOAD, provider)

    assert not product.ok
    assert "FAIL" in product.metadata.grounding_summary


def test_swpc_product_ok() -> None:
    """SWPC product → ok."""
    provider = MockLLMProvider(response="Solar activity is quiet.")
    product = SWPCDiscussionProduct().generate(PAYLOAD, provider)

    assert product.ok
    assert product.metadata.product_type == "swpc_discussion"


def test_swpc_product_grounding_fail() -> None:
    """SWPC product с галлюцинацией → not ok."""
    provider = MockLLMProvider(response="Kp observed: 99.9")
    product = SWPCDiscussionProduct().generate(PAYLOAD, provider)

    assert not product.ok


def test_product_metadata_duration() -> None:
    """Метаданные содержат duration_ms >= 0."""
    provider = MockLLMProvider(response="ok")
    product = ICAOBriefProduct().generate(PAYLOAD, provider)
    assert product.metadata.duration_ms >= 0


def test_product_metadata_grounding_ok() -> None:
    """Метаданные содержат grounding_ok."""
    provider = MockLLMProvider(response="ok")
    product = ICAOBriefProduct().generate(PAYLOAD, provider)
    assert product.metadata.grounding_ok is True


# --- Момент выпуска продукта ---


def test_product_issue_moment_is_the_payload_moment() -> None:
    """Выпуск датирован моментом данных: повторный прогон за ту же дату даёт тот же выпуск."""
    provider = MockLLMProvider(response="ok")
    product = ICAOBriefProduct().generate(PAYLOAD, provider)
    assert product.metadata.issued_utc == datetime(2026, 5, 12, tzinfo=UTC)


def test_product_without_issue_moment_in_payload_fails_loudly() -> None:
    """Нет metadata.issued_utc — нет момента выпуска: время не подставляется из часов."""
    provider = MockLLMProvider(response="ok")
    with pytest.raises(ValueError, match="metadata.issued_utc"):
        ICAOBriefProduct().generate({"metadata": {}}, provider)


def test_wrap_prefers_an_explicit_issue_moment() -> None:
    provider = MockLLMProvider(response="ok")
    product_type = ICAOBriefProduct()
    result = product_type._generate_via_llm(PAYLOAD, provider)
    moment = datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert product_type._wrap(result, issued_utc=moment).metadata.issued_utc == moment
