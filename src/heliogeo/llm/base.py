"""Абстрактный провайдер LLM."""

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """Ошибка LLM."""


class BaseLLMProvider(ABC):
    """Базовый интерфейс провайдера LLM."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        """Сгенерировать текст по промпту.

        Args:
            prompt: пользовательский промпт.
            system: системный промпт (роль).
            temperature: температура генерации.
            max_tokens: лимит токенов.
            **kwargs: доп. параметры провайдера.

        Returns:
            Сгенерированный текст.

        Raises:
            LLMError: при ошибке провайдера.
        """
