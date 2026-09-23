"""Продукт: SWPC Forecast Discussion."""

from typing import Any

from heliogeo.core.logging import get_logger
from heliogeo.llm.base import BaseLLMProvider
from heliogeo.products.base import BaseProduct, Product

logger = get_logger(__name__)


class SWPCDiscussionProduct(BaseProduct):
    """SWPC Forecast Discussion — свободный экспертный нарратив.

    Формат: экспертный, читаемый, полные предложения.
    """

    name = "swpc_discussion"
    language = "en"

    def generate(
        self,
        payload: dict[str, Any] | Any,
        provider: BaseLLMProvider,
        **kwargs: Any,
    ) -> Product:
        logger.info("Generating SWPC discussion")
        result = self._generate_via_llm(payload, provider, **kwargs)
        product = self._wrap(result)
        if not product.ok:
            logger.warning(
                "SWPC discussion grounding failed: %s", product.metadata.grounding_summary
            )
        return product
