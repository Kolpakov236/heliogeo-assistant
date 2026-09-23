"""Продукт: ICAO HANDOVER BRIEF."""

from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.llm.base import BaseLLMProvider
from heliogeo.products.base import BaseProduct, Product

logger = get_logger(__name__)


class ICAOBriefProduct(BaseProduct):
    """ICAO HANDOVER BRIEF — отчёт для авиации.

    Формат: строгий шаблон, английский, телеграфный стиль.
    """

    name = "icao_brief"
    language = "en"

    def generate(
        self,
        payload: dict[str, Any] | Any,
        provider: BaseLLMProvider,
        **kwargs: Any,
    ) -> Product:
        logger.info("Generating ICAO brief")
        result = self._generate_via_llm(payload, provider, **kwargs)
        product = self._wrap(result)
        if not product.ok:
            logger.warning("ICAO brief grounding failed: %s", product.metadata.grounding_summary)
        return product
