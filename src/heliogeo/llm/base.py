"""Абстрактный провайдер LLM."""

from abc import ABC, abstractmethod
from typing import Any, Literal

LLMErrorKind = Literal["timeout", "invalid_output", "validation", "config", "other"]


class LLMError(Exception):
    """Ошибка LLM.

    Attributes:
        kind: что случилось. ``timeout`` — модель не ответила (повтор может помочь).
            ``invalid_output`` — ответ не в ожидаемом формате (нужен ремонт, а не повтор).
            ``validation`` — текст не прошёл проверку на соответствие данным. ``config`` — нет
            провайдера или промпта. ``other`` — остальное.
    """

    def __init__(self, message: str = "", *, kind: LLMErrorKind = "other") -> None:
        super().__init__(message)
        self.kind = kind


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
