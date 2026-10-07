"""Тесты загрузки и рендера промптов."""

import json
from datetime import UTC, datetime

import pytest

from heliogeo.llm.base import LLMError
from heliogeo.llm.prompts import (
    load_prompt,
    render_prompt,
    render_prompt_for_payload,
)
from heliogeo.schema import Metadata, UnifiedPayload


def test_load_system_prompt() -> None:
    """system.txt существует и загружается."""
    text = load_prompt("system")
    assert "гелиогеофизик" in text.lower()
    assert "JSON" in text


def test_load_icao_prompt() -> None:
    """icao_brief.txt существует и содержит переменные Jinja2."""
    # Проверяем СЫРОЙ файл, а не результат рендера
    from pathlib import Path

    raw = (
        Path(__file__).resolve().parents[2] / "configs" / "llm" / "prompts" / "icao_brief.txt"
    ).read_text(encoding="utf-8")

    assert "ICAO" in raw
    assert "{{ payload_json }}" in raw
    assert "{{ issued_utc }}" in raw


def test_load_swpc_prompt() -> None:
    """swpc_discussion.txt существует."""
    text = load_prompt("swpc_discussion")
    assert "SWPC" in text


def test_load_nonexistent_prompt() -> None:
    """Несуществующий промпт → LLMError."""
    with pytest.raises(LLMError, match="Prompt not found") as caught:
        load_prompt("does_not_exist")
    assert caught.value.kind == "config"


def test_render_prompt_with_vars() -> None:
    """render_prompt подставляет переменные."""
    out = render_prompt("icao_brief", issued_utc="2026-05-12T00:00:00Z", payload_json="{}")
    assert "2026-05-12T00:00:00Z" in out
    assert "{}" in out


def test_render_prompt_for_payload() -> None:
    """render_prompt_for_payload работает с Pydantic-моделью."""
    now = datetime(2026, 5, 12, tzinfo=UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    out = render_prompt_for_payload("icao_brief", payload)
    assert "2026-05-12T00:00:00" in out
    assert "generator_version" in out


def test_render_prompt_for_payload_dict() -> None:
    """render_prompt_for_payload работает с dict."""
    data = {
        "metadata": {
            "issued_utc": "2026-05-12T00:00:00Z",
            "valid_until_utc": "2026-05-13T00:00:00Z",
            "generator_version": "1.0",
        }
    }
    out = render_prompt_for_payload("icao_brief", data)
    assert "2026-05-12T00:00:00Z" in out


def test_payload_json_is_valid_json() -> None:
    """payload_json — валидный JSON."""
    now = datetime(2026, 5, 12, tzinfo=UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    out = render_prompt_for_payload("icao_brief", payload)

    # Извлекаем JSON между первой { и последней }
    start = out.find("{")
    end = out.rfind("}")
    assert start != -1 and end != -1
    parsed = json.loads(out[start : end + 1])
    assert "metadata" in parsed


def test_render_prompt_for_payload_fills_the_header_from_metadata() -> None:
    """ISSUED и VALID UNTIL в шапке промпта — время из payload, а не пустые строки."""
    data = {
        "metadata": {
            "issued_utc": "2026-05-12T00:00:00Z",
            "valid_until_utc": "2026-05-13T12:00:00Z",
            "generator_version": "1.0",
        }
    }
    out = render_prompt_for_payload("icao_brief", data)
    assert "ISSUED: 2026-05-12T00:00:00Z" in out
    assert "VALID UNTIL: 2026-05-13T12:00:00Z" in out


def test_render_prompt_for_payload_model_fills_the_header() -> None:
    now = datetime(2026, 5, 12, tzinfo=UTC)
    payload = UnifiedPayload(metadata=Metadata(issued_utc=now, valid_until_utc=now))
    out = render_prompt_for_payload("icao_brief", payload)
    assert "ISSUED: 2026-05-12T00:00:00" in out
