"""Промпт отчёта за сутки: переменные шаблонов и сборка системного и пользовательского сообщений.

Тексты инструкций лежат в ``configs/llm/prompts`` (``discussion_system.txt`` — правила,
``daily_discussion.txt`` — формат, справочник полей и задание по разделам). Здесь только то, что
шаблон не может сделать сам: превратить идентификаторы запрещённых утверждений
(``forbids_claims`` из ``contracts/rules/signals.yaml``) в понятные модели формулировки и
отрендерить оба сообщения из одного словаря входа.

Новое запрещённое утверждение в контракте без формулировки здесь — ошибка: модель не должна
получать идентификатор, смысл которого ей неизвестен, а запрет не должен молча пропасть.
"""

from dataclasses import dataclass
from typing import Any

from heliogeo.llm.prompts import render_prompt_for_payload
from heliogeo.report.input import ReportInput

PRODUCT = "daily_discussion"
SYSTEM_PROMPT = "discussion_system"

# Формулировки без чисел: по ним модель понимает запрет, а проверка чисел не получает лишних
# «разрешённых» значений. Ключи — идентификаторы из ``forbids_claims`` контракта сигналов.
CLAIM_TEXT = {
    "no_earth_directed_cme": (
        "что выбросов, направленных к Земле, не было («No Earth-directed CMEs», «no CME was "
        "directed at Earth» и подобное): данных о выбросах нет или направление части выбросов "
        "не определено"
    ),
}


class ReportPromptError(Exception):
    """Промпт не собрать: во входе запрет, смысл которого модели не объяснён."""


@dataclass(frozen=True, kw_only=True)
class ReportPrompt:
    """Готовые сообщения для провайдера и словарь, из которого они получены.

    Attributes:
        system: системное сообщение (правила).
        user: пользовательское сообщение (формат, справочник, задание, JSON входа).
        payload: словарь входа с формулировками запретов; им же проверяются числа текста.
    """

    system: str
    user: str
    payload: dict[str, Any]


def prompt_payload(report: ReportInput) -> dict[str, Any]:
    """Словарь входа для шаблонов и проверки чисел: ``to_payload`` и тексты запретов.

    Raises:
        ReportPromptError: если во входе запрещённое утверждение без формулировки в ``CLAIM_TEXT``.
    """
    payload = report.to_payload()
    unknown = [claim for claim in payload["forbidden_claims"] if claim not in CLAIM_TEXT]
    if unknown:
        raise ReportPromptError(
            f"нет формулировки для запрещённых утверждений: {', '.join(unknown)}; "
            "добавьте их в CLAIM_TEXT"
        )
    payload["forbidden_claims_text"] = [CLAIM_TEXT[claim] for claim in payload["forbidden_claims"]]
    return payload


def build_report_prompt(report: ReportInput) -> ReportPrompt:
    """Системное и пользовательское сообщения для отчёта ``report``."""
    payload = prompt_payload(report)
    return ReportPrompt(
        system=render_prompt_for_payload(SYSTEM_PROMPT, payload),
        user=render_prompt_for_payload(PRODUCT, payload),
        payload=payload,
    )
