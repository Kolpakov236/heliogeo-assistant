"""Оркестрация пайплайна: build_payload → generate → grounding → render → deliver.

Чистые функции без планировщика. Запускается:
- из CLI: `heliogeo run --product all`
- из Cloud Functions: `handler(event, context)`
- из Workflows: через вызовы функций
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger
from heliogeo.llm.base import BaseLLMProvider, LLMError
from heliogeo.llm.generator import GenerationResult, generate_product
from heliogeo.llm.providers.alice import AliceLLMProvider
from heliogeo.llm.providers.mock import MockLLMProvider
from heliogeo.pipeline.builder import build_payload

logger = get_logger(__name__)


DEFAULT_PRODUCTS = ["icao_brief", "swpc_discussion"]


@dataclass
class PipelineStep:
    """Результат одного шага пайплайна."""

    name: str
    ok: bool
    duration_ms: int = 0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Результат полного прогона пайплайна."""

    ok: bool
    steps: list[PipelineStep] = field(default_factory=list)
    products: dict[str, GenerationResult] = field(default_factory=dict)
    error: str | None = None

    def summary(self) -> str:
        lines = [f"Pipeline: {'OK' if self.ok else 'FAIL'}"]
        for step in self.steps:
            status = "OK" if step.ok else "FAIL"
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

    raise LLMError(f"Unknown LLM provider: {name!r}")


def _empty_payload() -> dict[str, Any]:
    """Пустой payload для отладки пайплайна без данных."""
    now = datetime.now(UTC).isoformat()
    return {
        "metadata": {
            "issued_utc": now,
            "valid_until_utc": now,
            "generator_version": "1.0",
        },
        "solar_activity": {},
        "energetic_particles": {},
        "solar_wind": {},
        "geospace": {},
        "forecast": {},
        "images": {},
        "technical_issues": None,
    }


def build_payload_safe() -> tuple[dict[str, Any] | None, str | None]:
    """Собрать payload. Возвращает (payload, error).

    Обёртка над `pipeline.builder.build_payload`, которая не бросает.
    """
    try:
        payload = build_payload()
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json"), None
        if isinstance(payload, dict):
            return payload, None
        return None, f"Unexpected payload type: {type(payload).__name__}"
    except NotImplementedError:
        logger.warning("builder.build_payload not implemented yet, using empty payload")
        return _empty_payload(), None
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to build payload")
        return None, str(e)


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
) -> PipelineResult:
    """Прогнать пайплайн: build → generate → grounding → (render → deliver).

    Args:
        products: список продуктов. По умолчанию — DEFAULT_PRODUCTS.
        provider: провайдер LLM. По умолчанию — из Settings.
        fail_on_grounding: если True — бросать LLMError при провале grounding.
        deliver: если True — вызвать delivery (пока заглушка).

    Returns:
        PipelineResult с шагами и продуктами.
    """
    products = products or DEFAULT_PRODUCTS
    provider = provider or get_provider()

    result = PipelineResult(ok=True)

    # --- Шаг 1: build payload ---
    t0 = time.monotonic()
    payload, error = build_payload_safe()
    duration = int((time.monotonic() - t0) * 1000)

    if error or payload is None:
        result.ok = False
        result.error = error or "unknown build error"
        result.steps.append(
            PipelineStep(
                name="build_payload",
                ok=False,
                duration_ms=duration,
                error=result.error,
            )
        )
        logger.error("Pipeline failed at build_payload: %s", result.error)
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
            result.ok = False
            result.steps.append(
                PipelineStep(
                    name=f"generate_{product}",
                    ok=False,
                    duration_ms=duration,
                    error=str(e),
                )
            )
            logger.error("generate_%s failed: %s", product, e)
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
            )
        )
        if not gen.ok:
            result.ok = False

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
                result.ok = False
                result.steps.append(
                    PipelineStep(
                        name=f"deliver_{product}",
                        ok=False,
                        duration_ms=duration,
                        error=str(e),
                    )
                )
                logger.exception("deliver_%s failed", product)

    logger.info("Pipeline finished: %s", result.summary())
    return result
