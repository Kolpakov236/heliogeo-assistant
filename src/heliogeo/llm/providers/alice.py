"""Провайдер LLM для YandexGPT / Alice API.

ЗАГЛУШКА: полная реализация — в задании L5.
"""

from typing import Any

from heliogeo.llm.base import BaseLLMProvider, LLMError


class AliceLLMProvider(BaseLLMProvider):
    """Провайдер LLM через YandexGPT / Alice API."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise LLMError("AliceLLMProvider not implemented yet (task L5)", kind="config")

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        raise LLMError("AliceLLMProvider not implemented yet", kind="config")
