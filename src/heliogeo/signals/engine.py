"""Движок сигналов: строка признаков и правила контракта → сработавшие сигналы и итог.

Правила (``contracts/rules/signals.yaml``) описывают, что считать сигналом; здесь они применяются
к значениям. Три вида результата:

* **обычные сигналы** — условие над признаками выполнено, и качество каждого признака из условия
  допустимо для правила (``requires_quality``). Признак без значения условие не выполняет: пустое
  значение не равно ни норме, ни нулю;
* **«данных нет»** — у признака с ``on_missing`` нет значения. Признак, у которого пустота законна
  (``empty_ok_when``, например максимум скорости выбросов при нуле выбросов), такого сигнала не
  даёт: у него нет ``on_missing``;
* **итог** — ``all_clear`` («всё в норме») или ``partial_data`` («спокойно по имеющимся данным»):
  первый итоговый сигнал, у которого нет сигналов важности не ниже порога, а критичные данные
  в нужном состоянии. Если подходящего итога нет (тревоги или данных нет совсем), итога нет.

Движок чистый: не читает файлы, часы и сеть.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from heliogeo.contracts.signals import (
    Condition,
    Severity,
    SignalKind,
    SignalRule,
    SignalsContract,
    SummaryWhen,
)
from heliogeo.features.compute import FeatureRow

_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "gt": lambda value, argument: bool(value > argument),
    "gte": lambda value, argument: bool(value >= argument),
    "lt": lambda value, argument: bool(value < argument),
    "lte": lambda value, argument: bool(value <= argument),
    "eq": lambda value, argument: bool(value == argument),
    "ne": lambda value, argument: bool(value != argument),
    "in": lambda value, argument: value in argument,
}


@dataclass(frozen=True, kw_only=True)
class SignalSelection:
    """Результат применения правил к одной строке признаков.

    Attributes:
        fired: сработавшие сигналы: сначала обычные (в порядке контракта), затем «данных нет».
        summary: итоговый сигнал или ``None``.
        missing_features: признаки с ``on_missing``, у которых нет значения.
    """

    fired: tuple[SignalRule, ...]
    summary: SignalRule | None
    missing_features: tuple[str, ...]

    def ids(self) -> set[str]:
        """Идентификаторы сработавших сигналов (без итога)."""
        return {rule.id for rule in self.fired}

    def at_least(self, severity: Severity) -> tuple[SignalRule, ...]:
        """Сработавшие сигналы важности не ниже ``severity``."""
        return tuple(rule for rule in self.fired if rule.severity.at_least(severity))


def holds(condition: Condition, values: Mapping[str, Any]) -> bool:
    """Выполнено ли условие на значениях; признак без значения условия не выполняет."""
    if condition.feature is not None:
        value = values.get(condition.feature)
        if value is None:
            return False
        operator, argument = condition.operator()
        return _COMPARE[operator](value, argument)
    if condition.all_ is not None:
        return all(holds(child, values) for child in condition.all_)
    if condition.any_ is not None:
        return any(holds(child, values) for child in condition.any_)
    assert condition.not_ is not None
    return not holds(condition.not_, values)


def _quality_allows(rule: SignalRule, condition: Condition, row: FeatureRow) -> bool:
    """Все признаки условия имеют значение и допустимое для правила качество."""
    allowed = {quality.value for quality in rule.requires_quality}
    return all(
        row.values.get(name) is not None and row.quality.get(name) in allowed
        for name in condition.features()
    )


def _pick_summary(
    contract: SignalsContract, fired: tuple[SignalRule, ...], row: FeatureRow
) -> SignalRule | None:
    present = [row.values.get(name) is not None for name in contract.critical_features()]
    for rule in contract.signals:
        if rule.kind is not SignalKind.SUMMARY or not isinstance(rule.when, SummaryWhen):
            continue
        if any(f.severity.at_least(rule.when.no_signals_at_least) for f in fired):
            continue
        if rule.when.critical == "all_present" and all(present):
            return rule
        if rule.when.critical == "some_missing" and any(present) and not all(present):
            return rule
    return None


def select_signals(contract: SignalsContract, row: FeatureRow) -> SignalSelection:
    """Применить правила контракта к строке признаков."""
    regular = tuple(
        rule
        for rule in contract.signals
        if rule.kind is SignalKind.SIGNAL
        and isinstance(rule.when, Condition)
        and _quality_allows(rule, rule.when, row)
        and holds(rule.when, row.values)
    )

    by_id = {rule.id: rule for rule in contract.signals}
    missing = tuple(
        name
        for name, profile in contract.features.items()
        if profile.on_missing is not None and row.values.get(name) is None
    )
    unavailable: list[SignalRule] = []
    for name in missing:
        profile = contract.features[name]
        assert profile.on_missing is not None
        rule = by_id[profile.on_missing.signal]
        if rule not in unavailable:
            unavailable.append(rule)

    fired = (*regular, *unavailable)
    return SignalSelection(
        fired=fired, summary=_pick_summary(contract, fired, row), missing_features=missing
    )
