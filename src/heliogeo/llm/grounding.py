"""Grounding: проверка чисел в тексте LLM против JSON.

Идея: LLM не должна выдумывать числа. Если число есть в тексте,
но его нет в payload — это галлюцинация.
"""

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

# Числа: целые и с плавающей точкой, с опциональным минусом.
# Ловим 42, 3.14, -150, 1.5e+10.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

# Игнорируем числа, которые часто встречаются как "шум":
# годы, ISO-даты (2026-05-12), времена (03:15), версии (1.0).
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_TIME_RE = re.compile(r"\d{2}:\d{2}(?::\d{2})?")


@dataclass
class GroundingIssue:
    """Одно подозрительное число."""

    value: str
    context: str
    reason: str


@dataclass
class GroundingReport:
    """Результат проверки."""

    issues: list[GroundingIssue] = field(default_factory=list)
    checked_numbers: int = 0

    @property
    def ok(self) -> bool:
        """True, если галлюцинаций нет."""
        return not self.issues

    def summary(self) -> str:
        if self.ok:
            return f"OK ({self.checked_numbers} numbers checked)"
        return f"FAIL: {len(self.issues)} issue(s) из {self.checked_numbers} чисел"


def _collect_numbers_from_json(data: Any) -> set[float]:
    """Рекурсивно собрать все числа из JSON (dict/list/scalar)."""
    numbers: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int | float):
            numbers.add(float(node))
            return
        if isinstance(node, str):
            # Числа внутри строк (например, "M1.5", "N15W30") тоже собираем
            for m in _NUMBER_RE.finditer(node):
                with contextlib.suppress(ValueError):
                    numbers.add(float(m.group(0)))
            return
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
            return
        if isinstance(node, list | tuple):
            for v in node:
                walk(v)

    walk(data)
    return numbers


def _collect_numbers_from_text(text: str) -> list[tuple[str, str]]:
    """Собрать числа из текста вместе с контекстом (строка)."""
    results: list[tuple[str, str]] = []
    for line in text.splitlines():
        # Пропускаем строки с ISO-датами и временами целиком,
        # чтобы не вылавливать 2026, 05, 12, 03, 15
        if _ISO_DATE_RE.search(line) or _ISO_TIME_RE.search(line):
            # Всё равно собираем числа, но исключаем дату/время из них
            cleaned = _ISO_DATE_RE.sub(" ", line)
            cleaned = _ISO_TIME_RE.sub(" ", cleaned)
            for m in _NUMBER_RE.finditer(cleaned):
                results.append((m.group(0), line.strip()))
            continue
        for m in _NUMBER_RE.finditer(line):
            results.append((m.group(0), line.strip()))
    return results


def _numbers_match(a: float, b: float, rel_tol: float = 1e-3) -> bool:
    """Сравнить два числа с относительной точностью."""
    if a == b:
        return True
    return abs(a - b) <= rel_tol * max(abs(a), abs(b), 1.0)


def check_grounding(
    payload: dict[str, Any] | Any,
    text: str,
    rel_tol: float = 5e-2,
) -> GroundingReport:
    """Проверить, что все числа в тексте есть в payload.

    Args:
        payload: dict или Pydantic-модель.
        text: сгенерированный LLM текст.
        rel_tol: относительная точность сравнения (по умолчанию 0.1%).

    Returns:
        GroundingReport с issues (пусто → ok=True).
    """
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload

    json_numbers = _collect_numbers_from_json(data)
    text_numbers = _collect_numbers_from_text(text)

    report = GroundingReport(checked_numbers=len(text_numbers))

    for value_str, context in text_numbers:
        try:
            value = float(value_str)
        except ValueError:
            continue

        if any(_numbers_match(value, jn, rel_tol=rel_tol) for jn in json_numbers):
            continue

        report.issues.append(
            GroundingIssue(
                value=value_str,
                context=context,
                reason="not found in payload",
            )
        )

    return report


def payload_to_dict(payload: dict[str, Any] | Any) -> dict[str, Any]:
    """Утилита: привести payload к dict."""
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
        raise TypeError(f"model_dump returned {type(dumped)}, expected dict")
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be dict, got {type(payload)}")
    return payload


def dump_report_json(report: GroundingReport) -> str:
    """Сериализовать отчёт в JSON (для логов)."""
    return json.dumps(
        {
            "ok": report.ok,
            "checked_numbers": report.checked_numbers,
            "issues": [
                {"value": i.value, "context": i.context, "reason": i.reason} for i in report.issues
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
