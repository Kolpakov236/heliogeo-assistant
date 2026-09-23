"""Генерация продуктов через LLM с обязательным grounding."""

from dataclasses import dataclass, field
from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.llm.base import BaseLLMProvider, LLMError
from heliogeo.llm.grounding import GroundingReport, check_grounding
from heliogeo.llm.prompts import render_prompt_for_payload

logger = get_logger(__name__)


@dataclass
class GenerationResult:
    """Результат генерации продукта.

    Attributes:
        product: имя продукта (icao_brief, swpc_discussion).
        text: сгенерированный текст.
        grounding: отчёт grounding.
        payload_json: JSON-payload, который использовался.
        provider_name: имя провайдера.
        duration_ms: время генерации в мс.
    """

    product: str
    text: str
    grounding: GroundingReport
    payload_json: str = ""
    provider_name: str = ""
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True, если grounding прошёл."""
        return self.grounding.ok


def generate_product(
    product: str,
    payload: dict[str, Any] | Any,
    provider: BaseLLMProvider,
    *,
    system_prompt_name: str = "system",
    temperature: float = 0.2,
    max_tokens: int = 2000,
    fail_on_grounding: bool = False,
) -> GenerationResult:
    """Сгенерировать продукт через LLM и проверить grounding.

    Args:
        product: имя продукта (icao_brief, swpc_discussion).
        payload: UnifiedPayload или dict.
        provider: провайдер LLM (mock, alice, ...).
        system_prompt_name: имя системного промпта (по умолчанию "system").
        temperature: температура генерации.
        max_tokens: лимит токенов.
        fail_on_grounding: если True, бросает LLMError при провале grounding.

    Returns:
        GenerationResult с текстом и отчётом grounding.

    Raises:
        LLMError: при ошибке провайдера или (если fail_on_grounding=True)
                  при провале grounding.
    """
    import time

    start = time.monotonic()

    logger.info("Generating product: %s", product)

    system_prompt = render_prompt_for_payload(system_prompt_name, payload)
    user_prompt = render_prompt_for_payload(product, payload)

    try:
        text = provider.generate(
            prompt=user_prompt,
            system=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except LLMError:
        logger.exception("LLM provider failed for product %s", product)
        raise

    duration_ms = int((time.monotonic() - start) * 1000)

    grounding = check_grounding(payload, text)

    if grounding.ok:
        logger.info(
            "Product %s generated OK (%d numbers checked, %d ms)",
            product,
            grounding.checked_numbers,
            duration_ms,
        )
    else:
        logger.warning(
            "Product %s grounding FAILED: %s",
            product,
            grounding.summary(),
        )
        if fail_on_grounding:
            raise LLMError(f"Grounding failed for product {product}: {grounding.summary()}")

    payload_json = ""
    if hasattr(payload, "model_dump_json"):
        payload_json = payload.model_dump_json()
    else:
        import json

        payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    return GenerationResult(
        product=product,
        text=text,
        grounding=grounding,
        payload_json=payload_json,
        provider_name=type(provider).__name__,
        duration_ms=duration_ms,
    )


def generate_products(
    products: list[str],
    payload: dict[str, Any] | Any,
    provider: BaseLLMProvider,
    **kwargs: Any,
) -> list[GenerationResult]:
    """Сгенерировать несколько продуктов подряд.

    Args:
        products: список имён продуктов.
        payload: UnifiedPayload или dict.
        provider: провайдер LLM.
        **kwargs: передаются в generate_product.

    Returns:
        Список GenerationResult (в порядке products).
    """
    return [generate_product(product, payload, provider, **kwargs) for product in products]
