"""Тесты оркестратора пайплайна."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from heliogeo.extractors.swx.client import SwxClientError
from heliogeo.extractors.swx.extractor import SwxExtractorError
from heliogeo.llm import LLMError, MockLLMProvider
from heliogeo.pipeline.builder import BuilderError
from heliogeo.pipeline.pipeline import (
    RETRYABLE_ERROR_CLASSES,
    ErrorClass,
    PipelineResult,
    PipelineStep,
    build_payload_safe,
    classify_error,
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
        metadata=Metadata(issued_utc=now, valid_until_utc=now, source_products=[]),
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
    mock_payload.model_dump.return_value = {
        "metadata": {"issued_utc": "2026-09-01T12:00:00Z"}
    }
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
        payload, error = build_payload_safe(query_time=moment, window="1h", no_live=True)

    assert error is None
    assert payload is not None
    builder.assert_called_once_with(query_time=moment, window="1h", no_live=True)


def test_build_payload_safe_defaults_mean_now_with_live_data() -> None:
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        build_payload_safe()

    builder.assert_called_once_with(query_time=None, window=None, no_live=False)


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
            window="15m",
            no_live=True,
        )

    assert result.ok
    builder.assert_called_once_with(query_time=moment, window="15m", no_live=True)


def test_run_pipeline_defaults_mean_now_with_live_data() -> None:
    with patch(
        "heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()
    ) as builder:
        run_pipeline(products=["icao_brief"], provider=_mock_provider())

    builder.assert_called_once_with(query_time=None, window=None, no_live=False)


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


# --- Классы ошибок: по ним воркфлоу решает, повторять ли шаг ---


def _chained(outer: Exception, cause: Exception) -> Exception:
    """Исключение ``outer`` с причиной ``cause``, как после ``raise outer from cause``."""
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize(
    "exc,expected,retryable",
    [
        (SwxClientError("x", kind="unavailable"), ErrorClass.SOURCE_UNAVAILABLE, True),
        (SwxClientError("x", kind="not_found"), ErrorClass.DATA_INSUFFICIENT, False),
        (SwxClientError("x", kind="rejected"), ErrorClass.CONFIG_ERROR, False),
        (SwxClientError("x", kind="malformed"), ErrorClass.SOURCE_MALFORMED, False),
        (LLMError("x", kind="timeout"), ErrorClass.LLM_TIMEOUT, True),
        (LLMError("x", kind="invalid_output"), ErrorClass.LLM_INVALID_OUTPUT, False),
        (LLMError("x", kind="validation"), ErrorClass.VALIDATION_FAILED, False),
        (LLMError("x", kind="config"), ErrorClass.CONFIG_ERROR, False),
        (LLMError("x"), ErrorClass.INTERNAL, False),
        (SwxExtractorError("Payload validation failed"), ErrorClass.SOURCE_MALFORMED, False),
        (ValueError("boom"), ErrorClass.INTERNAL, False),
        (RuntimeError("boom"), ErrorClass.INTERNAL, False),
    ],
)
def test_classify_error(exc: Exception, expected: ErrorClass, retryable: bool) -> None:
    assert classify_error(exc) is expected
    assert (expected in RETRYABLE_ERROR_CLASSES) is retryable


def test_classify_error_follows_the_chain_of_causes() -> None:
    """Сборка payload оборачивает ошибки клиента: класс берётся у самой конкретной причины."""
    client_error = SwxClientError("503", kind="unavailable")
    extractor_error = _chained(SwxExtractorError("Failed to fetch snapshot"), client_error)
    builder_error = _chained(BuilderError("Failed to build payload"), extractor_error)

    assert classify_error(builder_error) is ErrorClass.SOURCE_UNAVAILABLE


def test_classify_error_survives_a_cycle_of_causes() -> None:
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first

    assert classify_error(first) is ErrorClass.INTERNAL


def test_error_classes_are_the_agreed_closed_set() -> None:
    assert {c.value for c in ErrorClass} == {
        "SOURCE_UNAVAILABLE",
        "SOURCE_MALFORMED",
        "TRANSIENT",
        "LLM_TIMEOUT",
        "LLM_INVALID_OUTPUT",
        "VALIDATION_FAILED",
        "DATA_INSUFFICIENT",
        "CONFIG_ERROR",
        "INTERNAL",
    }
    assert {c.value for c in RETRYABLE_ERROR_CLASSES} == {
        "SOURCE_UNAVAILABLE",
        "TRANSIENT",
        "LLM_TIMEOUT",
    }


def test_run_pipeline_source_unavailable_is_a_retryable_build_failure() -> None:
    error = _chained(
        BuilderError("Failed to build payload"), SwxClientError("503", kind="unavailable")
    )
    with patch("heliogeo.pipeline.pipeline.build_payload", side_effect=error):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider())

    assert not result.ok
    assert result.error_class is ErrorClass.SOURCE_UNAVAILABLE
    step = result.steps[0]
    assert (step.name, step.ok) == ("build_payload", False)
    assert step.error_class is ErrorClass.SOURCE_UNAVAILABLE
    assert step.retryable


def test_run_pipeline_malformed_source_is_not_retryable() -> None:
    """Ответ получен, но не прошёл схему: повтор не поможет, шаг не повторяют."""
    error = _chained(
        BuilderError("Failed to build payload"),
        SwxExtractorError("Payload validation failed: Kp > 9"),
    )
    with patch("heliogeo.pipeline.pipeline.build_payload", side_effect=error):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider())

    assert result.error_class is ErrorClass.SOURCE_MALFORMED
    assert not result.steps[0].retryable


def test_run_pipeline_unexpected_payload_type_is_internal() -> None:
    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=42):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider())

    assert result.error == "Unexpected payload type: int"
    assert result.error_class is ErrorClass.INTERNAL


def test_run_pipeline_grounding_failure_is_validation_failed() -> None:
    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider("Kp observed: 99.9"))

    step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert step.error_class is ErrorClass.VALIDATION_FAILED
    assert not step.retryable
    assert result.error_class is ErrorClass.VALIDATION_FAILED


def test_run_pipeline_provider_timeout_is_retryable() -> None:
    class SlowProvider(MockLLMProvider):
        def generate(self, *args: object, **kwargs: object) -> str:
            raise LLMError("model did not answer", kind="timeout")

    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()):
        result = run_pipeline(products=["icao_brief"], provider=SlowProvider())

    step = next(s for s in result.steps if s.name == "generate_icao_brief")
    assert step.error_class is ErrorClass.LLM_TIMEOUT
    assert step.retryable
    assert result.error_class is ErrorClass.LLM_TIMEOUT


def test_run_pipeline_keeps_the_class_of_the_first_error() -> None:
    """Классов несколько — у прогона класс первой ошибки, у каждого шага свой."""
    provider = MockLLMProvider(response="Kp observed: 99.9", fail_on_call=2)
    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()):
        result = run_pipeline(products=["icao_brief", "swpc_discussion"], provider=provider)

    first, second = (s for s in result.steps if s.name.startswith("generate_"))
    assert first.error_class is ErrorClass.VALIDATION_FAILED
    assert second.error_class is ErrorClass.INTERNAL  # «Mock failure» — LLMError вида other
    assert result.error_class is ErrorClass.VALIDATION_FAILED


def test_successful_run_has_no_error_class() -> None:
    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider())

    assert result.ok
    assert result.error_class is None
    assert all(step.error_class is None and not step.retryable for step in result.steps)


def test_summary_shows_the_class_of_a_failed_step() -> None:
    with patch("heliogeo.pipeline.pipeline.build_payload", return_value=_stable_payload()):
        result = run_pipeline(products=["icao_brief"], provider=_mock_provider("Kp observed: 99.9"))

    assert "generate_icao_brief: FAIL [VALIDATION_FAILED]" in result.summary()


def test_get_provider_unknown_is_a_config_error() -> None:
    with pytest.raises(LLMError) as caught:
        get_provider("nonexistent")

    assert classify_error(caught.value) is ErrorClass.CONFIG_ERROR
