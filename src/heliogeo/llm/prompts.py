"""Загрузка и рендер промптов."""

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from heliogeo.llm.base import LLMError

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PROJECT_ROOT / "configs" / "llm" / "prompts"

_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    autoescape=False,
    keep_trailing_newline=True,
)


def load_prompt(name: str) -> str:
    """Загрузить промпт по имени (без расширения .txt).

    Args:
        name: имя промпта, например "system", "icao_brief".

    Returns:
        Содержимое промпта.

    Raises:
        LLMError: если промпт не найден.
    """
    try:
        return _env.get_template(f"{name}.txt").render()
    except TemplateNotFound as e:
        raise LLMError(f"Prompt not found: {name}") from e


def render_prompt(name: str, **variables: Any) -> str:
    """Загрузить и отрендерить промпт с переменными.

    Args:
        name: имя промпта.
        **variables: переменные для подстановки.

    Returns:
        Отрендеренный промпт.

    Raises:
        LLMError: если промпт не найден.
    """
    try:
        template = _env.get_template(f"{name}.txt")
    except TemplateNotFound as e:
        raise LLMError(f"Prompt not found: {name}") from e

    return template.render(**variables)


def render_prompt_for_payload(
    name: str,
    payload: dict[str, Any] | Any,
) -> str:
    """Отрендерить промпт с payload (dict или Pydantic-модель).

    Args:
        name: имя промпта.
        payload: UnifiedPayload или dict.

    Returns:
        Отрендеренный промпт.
    """
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload

    payload_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    # Разворачиваем верхний уровень для удобства в Jinja2
    flat: dict[str, Any] = {"payload_json": payload_json}
    if isinstance(data, dict):
        flat.update(data)

    return render_prompt(name, **flat)
