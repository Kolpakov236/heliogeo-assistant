"""Mock-провайдер LLM: возвращает заранее заданные ответы."""

from typing import Any

from heliogeo.llm.base import BaseLLMProvider, LLMError


class MockLLMProvider(BaseLLMProvider):
    """Mock-провайдер для тестов и отладки.

    Возвращает заранее заданный ответ, либо эхо промпта.
    """

    def __init__(
        self,
        response: str | None = None,
        responses: list[str] | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        """
        Args:
            response: фиксированный ответ (если один).
            responses: очередь ответов (если несколько).
            fail_on_call: номер вызова, на котором бросить LLMError (1-based).
        """
        self._response = response
        self._responses = list(responses) if responses else []
        self._fail_on_call = fail_on_call
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._call_count += 1
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )

        if self._fail_on_call is not None and self._call_count == self._fail_on_call:
            raise LLMError(f"Mock failure on call {self._call_count}")

        if self._responses:
            return self._responses.pop(0)
        if self._response is not None:
            return self._response
        return f"[MOCK] {prompt[:100]}"
