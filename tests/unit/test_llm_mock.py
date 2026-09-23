"""Тесты mock-провайдера."""

import pytest

from heliogeo.llm import LLMError, MockLLMProvider


def test_mock_default_response() -> None:
    """Без response → эхо промпта."""
    provider = MockLLMProvider()
    out = provider.generate("Hello")
    assert "[MOCK]" in out


def test_mock_fixed_response() -> None:
    """Фиксированный ответ."""
    provider = MockLLMProvider(response="OK")
    assert provider.generate("any") == "OK"
    assert provider.generate("any") == "OK"


def test_mock_queue_responses() -> None:
    """Очередь ответов."""
    provider = MockLLMProvider(responses=["first", "second"])
    assert provider.generate("a") == "first"
    assert provider.generate("b") == "second"


def test_mock_records_calls() -> None:
    """Провайдер записывает вызовы."""
    provider = MockLLMProvider(response="x")
    provider.generate("prompt1", system="sys", temperature=0.5, max_tokens=100)
    provider.generate("prompt2")

    assert len(provider.calls) == 2
    assert provider.calls[0]["prompt"] == "prompt1"
    assert provider.calls[0]["system"] == "sys"
    assert provider.calls[0]["temperature"] == 0.5
    assert provider.calls[0]["max_tokens"] == 100


def test_mock_fail_on_call() -> None:
    """Провайдер падает на заданном вызове."""
    provider = MockLLMProvider(response="ok", fail_on_call=2)
    assert provider.generate("a") == "ok"
    with pytest.raises(LLMError, match="Mock failure on call 2"):
        provider.generate("b")
