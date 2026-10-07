"""Оркестрация пайплайна: build_payload → generate → grounding → render → deliver.

Чистые функции без планировщика. Запускается:
- из CLI: `heliogeo run --product all` (локальный сквозной прогон одним вызовом)
- из Cloud Functions: `handler(event, context)`, каждая функция — своя часть пайплайна
- из Workflows: шаги воркфлоу вызывают функции (см. deploy/workflows)

Упавший шаг несёт класс ошибки (`ErrorClass`): по нему воркфлоу решает, повторять ли шаг.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger
from heliogeo.extractors.swx.client import SwxClientError
from heliogeo.extractors.swx.extractor import SwxExtractorError
from heliogeo.llm.base import BaseLLMProvider, LLMError
from heliogeo.llm.generator import GenerationResult, generate_product
from heliogeo.llm.providers.alice import AliceLLMProvider
from heliogeo.llm.providers.mock import MockLLMProvider
from heliogeo.pipeline.builder import build_payload

logger = get_logger(__name__)


DEFAULT_PRODUCTS = ["icao_brief", "swpc_discussion"]


class ErrorClass(StrEnum):
    """Закрытый перечень классов ошибок шага.

    По классу решают, повторять ли шаг: функция возвращает его в ответе, а проверки ответа в
    deploy/workflows принимают решение. Различие принципиальное: недоступность источника лечится
    повтором, а файл, который скачался, но не разбирается, повтором только тратит время и деньги.
    Классы, которые встречаются в спецификации воркфлоу, обязаны быть из этого перечня — это
    проверяет тест.
    """

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"  # источник не ответил (сеть, 5xx): повтор лечит
    SOURCE_MALFORMED = "SOURCE_MALFORMED"  # ответ получен, но не разбирается
    TRANSIENT = "TRANSIENT"  # временный сбой (таймаут сети, троттлинг)
    LLM_TIMEOUT = "LLM_TIMEOUT"  # модель не ответила
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"  # ответ модели не в формате: ремонт, не повтор
    VALIDATION_FAILED = "VALIDATION_FAILED"  # текст не прошёл проверку: ремонт, не повтор
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"  # нет данных за дату: деградация, не отказ
    CONFIG_ERROR = "CONFIG_ERROR"  # неверная настройка: нет провайдера, промпта, адреса
    INTERNAL = "INTERNAL"  # непойманное исключение


# Классы, при которых повтор шага может помочь; остальные повтором не лечатся.
RETRYABLE_ERROR_CLASSES = frozenset(
    {ErrorClass.SOURCE_UNAVAILABLE, ErrorClass.TRANSIENT, ErrorClass.LLM_TIMEOUT}
)

_SWX_ERROR_CLASSES = {
    "unavailable": ErrorClass.SOURCE_UNAVAILABLE,
    "not_found": ErrorClass.DATA_INSUFFICIENT,
    "rejected": ErrorClass.CONFIG_ERROR,
    "malformed": ErrorClass.SOURCE_MALFORMED,
}
_LLM_ERROR_CLASSES = {
    "timeout": ErrorClass.LLM_TIMEOUT,
    "invalid_output": ErrorClass.LLM_INVALID_OUTPUT,
    "validation": ErrorClass.VALIDATION_FAILED,
    "config": ErrorClass.CONFIG_ERROR,
    "other": ErrorClass.INTERNAL,
}


def classify_error(exc: BaseException) -> ErrorClass:
    """Класс ошибки по исключению и цепочке его причин (``raise ... from ...``).

    Берётся самое конкретное из известного: ошибки клиента источника и LLM несут ``kind``; ошибка
    сборки payload без такой причины (например, ответ источника не прошёл схему) — это
    SOURCE_MALFORMED; всё остальное — INTERNAL.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for error in chain:
        if isinstance(error, SwxClientError):
            return _SWX_ERROR_CLASSES[error.kind]
        if isinstance(error, LLMError):
            return _LLM_ERROR_CLASSES[error.kind]
    if any(isinstance(error, SwxExtractorError) for error in chain):
        return ErrorClass.SOURCE_MALFORMED
    return ErrorClass.INTERNAL


@dataclass
class PipelineStep:
    """Результат одного шага пайплайна.

    Attributes:
        error_class: класс ошибки у упавшего шага, у успешного — ``None``.
    """

    name: str
    ok: bool
    duration_ms: int = 0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error_class: ErrorClass | None = None

    @property
    def retryable(self) -> bool:
        """Может ли помочь повтор шага (у успешного шага — всегда False)."""
        return self.error_class in RETRYABLE_ERROR_CLASSES


@dataclass
class PipelineResult:
    """Результат полного прогона пайплайна.

    Attributes:
        error_class: класс первой ошибки прогона (``None``, если ошибок не было).
    """

    ok: bool
    steps: list[PipelineStep] = field(default_factory=list)
    products: dict[str, GenerationResult] = field(default_factory=dict)
    error: str | None = None
    error_class: ErrorClass | None = None

    def summary(self) -> str:
        lines = [f"Pipeline: {'OK' if self.ok else 'FAIL'}"]
        for step in self.steps:
            status = "OK" if step.ok else "FAIL"
            if step.error_class is not None:
                status += f" [{step.error_class}]"
            lines.append(f"  {step.name}: {status} ({step.duration_ms} ms)")
        if self.error:
            lines.append(f"  error: {self.error}")
        return "\n".join(lines)


def get_provider(name: str | None = None) -> BaseLLMProvider:
    """Получить провайдер LLM по имени.

    Args:
        name: "alice" | "mock" | None (по умолчанию из Settings).

    Returns:
        Экземпляр провайдера.

    Raises:
        LLMError: если имя провайдера неизвестно.
    """
    if name is None:
        name = get_settings().llm_provider

    if name == "mock":
        return MockLLMProvider(response="No data available.")

    if name == "alice":
        return AliceLLMProvider()

    raise LLMError(f"Unknown LLM provider: {name!r}", kind="config")


def build_payload_safe(
    query_time: datetime | None = None,
    no_live: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """Собрать payload. Возвращает (payload, error).

    Обёртка над `pipeline.builder.build_payload`, которая не бросает.

    Args:
        query_time: момент UTC с часовым поясом. None → «сейчас» по часам клиента источника
            (пайплайн системных часов не читает).
        no_live: не подмешивать live-данные SWPC (для воспроизводимых прогонов по архиву).
    """
    payload, error = _build_payload(query_time, no_live)
    return payload, None if error is None else str(error)


def _build_payload(
    query_time: datetime | None,
    no_live: bool,
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Собрать payload; вместо исключения возвращается оно само — по нему определяется класс."""
    try:
        payload = build_payload(query_time=query_time, no_live=no_live)
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json"), None
        if isinstance(payload, dict):
            return payload, None
        return None, TypeError(f"Unexpected payload type: {type(payload).__name__}")
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to build payload")
        return None, e


def _deliver(product: str, gen: GenerationResult) -> None:
    """Заглушка delivery. Реализация — в Спринте 6."""
    logger.info(
        "Would deliver product=%s, provider=%s, text_len=%d",
        product,
        gen.provider_name,
        len(gen.text),
    )


def run_pipeline(
    products: list[str] | None = None,
    provider: BaseLLMProvider | None = None,
    *,
    fail_on_grounding: bool = False,
    deliver: bool = False,
    query_time: datetime | None = None,
    no_live: bool = False,
) -> PipelineResult:
    """Прогнать пайплайн: build → generate → grounding → (render → deliver).

    Args:
        products: список продуктов. По умолчанию — DEFAULT_PRODUCTS.
        provider: провайдер LLM. По умолчанию — из Settings.
        fail_on_grounding: если True — бросать LLMError при провале grounding.
        deliver: если True — вызвать delivery (пока заглушка).
        query_time: момент данных UTC с часовым поясом. None → «сейчас» по часам клиента
            источника (пайплайн системных часов не читает).
        no_live: не подмешивать live-данные SWPC (для воспроизводимых прогонов по архиву).

    Returns:
        PipelineResult с шагами и продуктами.
    """
    products = products or DEFAULT_PRODUCTS
    provider = provider or get_provider()

    result = PipelineResult(ok=True)

    # --- Шаг 1: build payload ---
    t0 = time.monotonic()
    payload, build_error = _build_payload(query_time, no_live)
    duration = int((time.monotonic() - t0) * 1000)

    if build_error is not None or payload is None:
        error_class = (
            classify_error(build_error) if build_error is not None else ErrorClass.INTERNAL
        )
        result.ok = False
        result.error = (str(build_error) if build_error is not None else "") or (
            "unknown build error"
        )
        result.error_class = error_class
        result.steps.append(
            PipelineStep(
                name="build_payload",
                ok=False,
                duration_ms=duration,
                error=result.error,
                error_class=error_class,
            )
        )
        logger.error("Pipeline failed at build_payload [%s]: %s", error_class, result.error)
        return result

    result.steps.append(
        PipelineStep(
            name="build_payload",
            ok=True,
            duration_ms=duration,
            data={"payload_keys": list(payload.keys())},
        )
    )

    # --- Шаг 2: generate products ---
    for product in products:
        t0 = time.monotonic()
        try:
            gen = generate_product(
                product,
                payload,
                provider,
                fail_on_grounding=fail_on_grounding,
            )
        except LLMError as e:
            duration = int((time.monotonic() - t0) * 1000)
            error_class = classify_error(e)
            result.ok = False
            result.error_class = result.error_class or error_class
            result.steps.append(
                PipelineStep(
                    name=f"generate_{product}",
                    ok=False,
                    duration_ms=duration,
                    error=str(e),
                    error_class=error_class,
                )
            )
            logger.error("generate_%s failed [%s]: %s", product, error_class, e)
            continue

        duration = int((time.monotonic() - t0) * 1000)
        result.products[product] = gen
        result.steps.append(
            PipelineStep(
                name=f"generate_{product}",
                ok=gen.ok,
                duration_ms=duration,
                error=None if gen.ok else gen.grounding.summary(),
                data={
                    "grounding_ok": gen.ok,
                    "checked_numbers": gen.grounding.checked_numbers,
                    "issues": len(gen.grounding.issues),
                },
                error_class=None if gen.ok else ErrorClass.VALIDATION_FAILED,
            )
        )
        if not gen.ok:
            result.ok = False
            result.error_class = result.error_class or ErrorClass.VALIDATION_FAILED

    # --- Шаг 3 (опционально): deliver ---
    if deliver:
        for product, gen in result.products.items():
            if not gen.ok:
                logger.warning("Skip delivery of %s: grounding failed", product)
                continue
            t0 = time.monotonic()
            try:
                _deliver(product, gen)
                duration = int((time.monotonic() - t0) * 1000)
                result.steps.append(
                    PipelineStep(
                        name=f"deliver_{product}",
                        ok=True,
                        duration_ms=duration,
                    )
                )
            except Exception as e:  # noqa: BLE001
                duration = int((time.monotonic() - t0) * 1000)
                error_class = classify_error(e)
                result.ok = False
                result.error_class = result.error_class or error_class
                result.steps.append(
                    PipelineStep(
                        name=f"deliver_{product}",
                        ok=False,
                        duration_ms=duration,
                        error=str(e),
                        error_class=error_class,
                    )
                )
                logger.exception("deliver_%s failed", product)

    logger.info("Pipeline finished: %s", result.summary())
    return result
