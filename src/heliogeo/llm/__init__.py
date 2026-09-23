"""LLM-слой: провайдеры, промпты, grounding."""

from heliogeo.llm.base import BaseLLMProvider, LLMError
from heliogeo.llm.grounding import (
    GroundingIssue,
    GroundingReport,
    check_grounding,
    dump_report_json,
)
from heliogeo.llm.prompts import (
    load_prompt,
    render_prompt,
    render_prompt_for_payload,
)
from heliogeo.llm.providers.mock import MockLLMProvider

__all__ = [
    "BaseLLMProvider",
    "GroundingIssue",
    "GroundingReport",
    "LLMError",
    "MockLLMProvider",
    "check_grounding",
    "dump_report_json",
    "load_prompt",
    "render_prompt",
    "render_prompt_for_payload",
]
