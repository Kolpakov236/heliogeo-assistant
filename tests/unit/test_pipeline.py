"""Тесты оркестратора пайплайна."""

from unittest.mock import patch

import pytest

from heliogeo.llm import LLMError, MockLLMProvider
from heliogeo.pipeline.pipeline import (
    PipelineResult,
    PipelineStep,
    build_payload_safe,
    get_provider,
    run_pipeline,
)


def _mock_provider(response: str = "No data available.") -> MockLLMProvider:
    return MockLLMProvider(response=response)


def test_get_provider_mock() -> None:
    """get_provider('mock') возвращает MockLLMProvider."""
    p = get_provider("mock")
    assert isinstance(p, MockLLMProvider)


def test_get_provider_unknown() -> None:
    """get_provider('неизвестный') → LLMError."""
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        get_provider("nonexistent")


def test_build_payload_safe_not_implemented() -> None:
    """build_payload_safe при NotImplementedError → пустой payload."""
    with patch("heliogeo.pipeline.pipeline.build_payload", side_effect=NotImplementedError):
        payload, error = build_payload_safe()
    assert error is None
    assert payload is not None
    assert "metadata" in payload


def test_build_payload_safe_returns_dict() -> None:
    """build_payload_safe возвращает dict."""
    payload, error = build_payload_safe()
    assert error is None
    assert isinstance(payload, dict)
    assert "metadata" in payload


def test_run_pipeline_default_products() -> None:
    """run_pipeline на mock → ok, оба продукта сгенерированы."""
    provider = _mock_provider("No data available.")
    result = run_pipeline(provider=provider)

    assert isinstance(result, PipelineResult)
    assert result.ok
    assert len(result.steps) >= 3  # build + 2 generate
    assert "icao_brief" in result.products
    assert "swpc_discussion" in result.products


def test_run_pipeline_single_product() -> None:
    """run_pipeline с одним продуктом."""
    provider = _mock_provider("No data available.")
    result = run_pipeline(products=["icao_brief"], provider=provider)

    assert result.ok
    assert len(result.products) == 1
    assert "icao_brief" in result.products
    assert "swpc_discussion" not in result.products


def test_run_pipeline_grounding_failure() -> None:
    """Mock возвращает галлюцинацию → result.ok = False, шаг FAIL."""
    provider = _mock_provider("Kp observed: 99.9")  # 99.9 нет в payload
    result = run_pipeline(products=["icao_brief"], provider=provider)

    assert not result.ok
    gen_step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert not gen_step.ok
    assert "FAIL" in (gen_step.error or "")


def test_run_pipeline_fail_on_grounding() -> None:
    """fail_on_grounding=True — не бросает, а помечает как failed."""
    provider = _mock_provider("Kp observed: 99.9")
    result = run_pipeline(
        products=["icao_brief"],
        provider=provider,
        fail_on_grounding=True,
    )
    assert not result.ok
    # Всё равно не бросает, а возвращает result с ошибкой
    assert len(result.steps) >= 2


def test_run_pipeline_provider_error() -> None:
    """Ошибка провайдера — шаг FAIL, но pipeline не падает."""
    provider = MockLLMProvider(response="ok", fail_on_call=1)
    result = run_pipeline(products=["icao_brief"], provider=provider)

    assert not result.ok
    gen_step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert not gen_step.ok
    assert "Mock failure" in (gen_step.error or "")


def test_run_pipeline_summary() -> None:
    """summary() даёт читаемый текст."""
    provider = _mock_provider("No data available.")
    result = run_pipeline(products=["icao_brief"], provider=provider)
    summary = result.summary()

    assert "Pipeline: OK" in summary
    assert "build_payload" in summary
    assert "generate_icao_brief" in summary


def test_run_pipeline_deliver_flag() -> None:
    """deliver=True — добавляет шаг deliver_*."""
    provider = _mock_provider("No data available.")
    result = run_pipeline(
        products=["icao_brief"],
        provider=provider,
        deliver=True,
    )
    assert result.ok
    deliver_steps = [s for s in result.steps if s.name.startswith("deliver_")]
    assert len(deliver_steps) == 1
    assert deliver_steps[0].ok


def test_run_pipeline_deliver_skips_failed_grounding() -> None:
    """deliver=True, но grounding упал — delivery пропускается."""
    provider = _mock_provider("Kp observed: 99.9")
    result = run_pipeline(
        products=["icao_brief"],
        provider=provider,
        deliver=True,
    )
    assert not result.ok
    deliver_steps = [s for s in result.steps if s.name.startswith("deliver_")]
    assert len(deliver_steps) == 0  # delivery пропущен


def test_pipeline_step_dataclass() -> None:
    """PipelineStep — dataclass."""
    step = PipelineStep(name="test", ok=True, duration_ms=100)
    assert step.name == "test"
    assert step.ok
    assert step.duration_ms == 100
    assert step.error is None
    assert step.data == {}


def test_pipeline_result_defaults() -> None:
    """PipelineResult с дефолтами."""
    result = PipelineResult(ok=True)
    assert result.steps == []
    assert result.products == {}
    assert result.error is None
