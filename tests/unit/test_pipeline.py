"""Тесты оркестратора пайплайна."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from heliogeo.llm import LLMError, MockLLMProvider
from heliogeo.pipeline.pipeline import (
    PipelineResult,
    PipelineStep,
    build_payload_safe,
    get_provider,
    run_pipeline,
)
from heliogeo.schema import Metadata, UnifiedPayload


def _mock_provider(response: str = "No data available.") -> MockLLMProvider:
    return MockLLMProvider(response=response)


def _stable_payload() -> UnifiedPayload:
    """Стабильный payload для тестов."""
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    return UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )


# --- get_provider ---


def test_get_provider_mock() -> None:
    """get_provider('mock') возвращает MockLLMProvider."""
    p = get_provider("mock")
    assert isinstance(p, MockLLMProvider)


def test_get_provider_unknown() -> None:
    """get_provider('неизвестный') → LLMError."""
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        get_provider("nonexistent")


# --- build_payload ---


def test_build_payload_calls_extractor() -> None:
    """build_payload вызывает SwxExtractor.extract."""
    from heliogeo.pipeline.builder import build_payload

    mock_extractor = MagicMock()
    mock_payload = MagicMock()
    mock_payload.model_dump.return_value = {"metadata": {}}
    mock_extractor.extract.return_value = (mock_payload, None)

    with patch(
        "heliogeo.pipeline.builder.SwxExtractor",
        return_value=mock_extractor,
    ):
        result = build_payload()

    assert result is mock_payload
    mock_extractor.extract.assert_called_once()


def test_build_payload_safe_with_mock() -> None:
    """build_payload_safe возвращает dict с mock-экстрактором."""
    mock_extractor = MagicMock()
    mock_payload = MagicMock()
    mock_payload.model_dump.return_value = {"metadata": {"issued_utc": "2026-09-01T12:00:00Z"}}
    mock_extractor.extract.return_value = (mock_payload, None)

    with patch(
        "heliogeo.pipeline.builder.SwxExtractor",
        return_value=mock_extractor,
    ):
        payload, error = build_payload_safe()

    assert error is None
    assert payload is not None
    assert "metadata" in payload


def test_build_payload_safe_forwards_moment_and_no_live() -> None:
    """Момент данных и no_live доходят до build_payload."""
    moment = datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        payload, error = build_payload_safe(query_time=moment, no_live=True)

    assert error is None
    assert payload is not None
    builder.assert_called_once_with(query_time=moment, no_live=True)


def test_build_payload_safe_defaults_mean_now_with_live_data() -> None:
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        build_payload_safe()

    builder.assert_called_once_with(query_time=None, no_live=False)


# --- run_pipeline (с mock-билдером) ---


def test_run_pipeline_forwards_moment_and_no_live() -> None:
    """run_pipeline передаёт query_time и no_live сборщику payload."""
    moment = datetime(2026, 9, 1, 12, tzinfo=UTC)
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        result = run_pipeline(
            products=["icao_brief"],
            provider=_mock_provider(),
            query_time=moment,
            no_live=True,
        )

    assert result.ok
    builder.assert_called_once_with(query_time=moment, no_live=True)


def test_run_pipeline_defaults_mean_now_with_live_data() -> None:
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        run_pipeline(products=["icao_brief"], provider=_mock_provider())

    builder.assert_called_once_with(query_time=None, no_live=False)


def test_run_pipeline_reports_a_failed_build_with_a_naive_moment() -> None:
    """Наивное время — ошибка шага build_payload, а не падение пайплайна."""
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        side_effect=ValueError("query_time должен быть с часовым поясом"),
    ):
        result = run_pipeline(
            products=["icao_brief"],
            provider=_mock_provider(),
            query_time=datetime(2026, 9, 1, 12),
        )

    assert not result.ok
    assert result.error is not None
    assert "часовым поясом" in result.error


def test_run_pipeline_default_products() -> None:
    """run_pipeline на mock → ok, оба продукта сгенерированы."""
    provider = _mock_provider("No data available.")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(provider=provider)

    assert isinstance(result, PipelineResult)
    assert result.ok
    assert len(result.steps) >= 3
    assert "icao_brief" in result.products
    assert "swpc_discussion" in result.products


def test_run_pipeline_single_product() -> None:
    """run_pipeline с одним продуктом."""
    provider = _mock_provider("No data available.")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(products=["icao_brief"], provider=provider)

    assert result.ok
    assert len(result.products) == 1
    assert "icao_brief" in result.products
    assert "swpc_discussion" not in result.products


def test_run_pipeline_grounding_failure() -> None:
    """Mock возвращает галлюцинацию → result.ok = False, шаг FAIL."""
    provider = _mock_provider("Kp observed: 99.9")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(products=["icao_brief"], provider=provider)

    assert not result.ok
    gen_step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert not gen_step.ok
    assert "FAIL" in (gen_step.error or "")


def test_run_pipeline_fail_on_grounding() -> None:
    """fail_on_grounding=True — не бросает, а помечает как failed."""
    provider = _mock_provider("Kp observed: 99.9")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(
            products=["icao_brief"],
            provider=provider,
            fail_on_grounding=True,
        )

    assert not result.ok
    assert len(result.steps) >= 2


def test_run_pipeline_provider_error() -> None:
    """Ошибка провайдера — шаг FAIL, но pipeline не падает."""
    provider = MockLLMProvider(response="ok", fail_on_call=1)
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(products=["icao_brief"], provider=provider)

    assert not result.ok
    gen_step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert not gen_step.ok
    assert "Mock failure" in (gen_step.error or "")


def test_run_pipeline_summary() -> None:
    """summary() даёт читаемый текст."""
    provider = _mock_provider("No data available.")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(products=["icao_brief"], provider=provider)

    summary = result.summary()
    assert "Pipeline: OK" in summary
    assert "build_payload" in summary
    assert "generate_icao_brief" in summary


def test_run_pipeline_deliver_flag() -> None:
    """deliver=True — добавляет шаг deliver_*."""
    provider = _mock_provider("No data available.")
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
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
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=_stable_payload(),
    ):
        result = run_pipeline(
            products=["icao_brief"],
            provider=provider,
            deliver=True,
        )

    assert not result.ok
    deliver_steps = [s for s in result.steps if s.name.startswith("deliver_")]
    assert len(deliver_steps) == 0


# --- dataclasses ---


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
