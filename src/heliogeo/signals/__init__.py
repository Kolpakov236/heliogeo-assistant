"""Сигналы: применение правил ``contracts/rules/signals.yaml`` к строке признаков.

Контракт (``heliogeo.contracts.signals``) описывает правила, а ``select_signals`` выбирает по ним
сработавшие сигналы, сигналы «данных нет» и итог («всё в норме» или «спокойно по неполным данным»).
"""

from heliogeo.signals.engine import SignalSelection, holds, select_signals

__all__ = ["SignalSelection", "holds", "select_signals"]
