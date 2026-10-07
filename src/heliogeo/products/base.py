"""Базовый интерфейс продукта."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from heliogeo.llm.base import BaseLLMProvider
from heliogeo.llm.generator import GenerationResult, generate_product


@dataclass
class ProductMetadata:
    """Метаданные продукта."""

    product_type: str
    issued_utc: datetime
    generator_version: str = "1.0"
    provider_name: str = ""
    duration_ms: int = 0
    grounding_ok: bool = False
    grounding_summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Product:
    """Готовый продукт."""

    metadata: ProductMetadata
    text: str
    language: str = "en"

    @property
    def ok(self) -> bool:
        """Продукт готов к доставке."""
        return self.metadata.grounding_ok


class BaseProduct(ABC):
    """Базовый класс продукта.

    Продукт знает:
    - имя (product_type)
    - промпт (имя Jinja2-шаблона)
    - язык
    - как сгенерировать текст
    """

    name: str
    language: str = "en"

    @abstractmethod
    def generate(
        self,
        payload: dict[str, Any] | Any,
        provider: BaseLLMProvider,
        **kwargs: Any,
    ) -> Product:
        """Сгенерировать продукт.

        Args:
            payload: UnifiedPayload или dict.
            provider: провайдер LLM.
            **kwargs: доп. параметры (temperature, max_tokens, ...).

        Returns:
            Product с текстом и метаданными.
        """
        raise NotImplementedError

    def _wrap(
        self,
        result: GenerationResult,
        issued_utc: datetime | None = None,
    ) -> Product:
        """Обернуть GenerationResult в Product.

        Момент выпуска — явный ``issued_utc`` либо ``result.issued_utc`` (момент данных из payload).
        Системные часы не читаются: иначе повторный прогон за ту же дату давал бы другой выпуск.

        Raises:
            ValueError: если момент выпуска неоткуда взять (в payload нет ``metadata.issued_utc``).
        """
        issued = issued_utc or result.issued_utc
        if issued is None:
            raise ValueError(
                f"Не задан момент выпуска продукта {self.name}: в payload нет metadata.issued_utc"
            )
        return Product(
            metadata=ProductMetadata(
                product_type=self.name,
                issued_utc=issued,
                generator_version="1.0",
                provider_name=result.provider_name,
                duration_ms=result.duration_ms,
                grounding_ok=result.ok,
                grounding_summary=result.grounding.summary(),
            ),
            text=result.text,
            language=self.language,
        )

    def _generate_via_llm(
        self,
        payload: dict[str, Any] | Any,
        provider: BaseLLMProvider,
        **kwargs: Any,
    ) -> GenerationResult:
        """Вызвать generate_product для этого продукта."""
        return generate_product(
            self.name,
            payload,
            provider,
            **kwargs,
        )
