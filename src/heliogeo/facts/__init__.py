"""Хранилище фактов: единственный источник значений для остальных компонентов.

Факт — это значение вместе с происхождением, качеством, окном валидности и политикой
отображения. Отсутствие данных выражается фактом со статусом ``missing``, а не значением ``None``.
Модель и инварианты — ``heliogeo.facts.model``, правила форматирования чисел —
``heliogeo.facts.formatting``, описание — docs/facts.md.
"""

from heliogeo.facts.formatting import DEFAULT_LANG, LANGUAGES, Lang
from heliogeo.facts.model import (
    FACT_ID_PATTERN,
    DisplayPolicy,
    Fact,
    FactKind,
    Provenance,
    QCStatus,
    Uncertainty,
)

__all__ = [
    "DEFAULT_LANG",
    "FACT_ID_PATTERN",
    "LANGUAGES",
    "DisplayPolicy",
    "Fact",
    "FactKind",
    "Lang",
    "Provenance",
    "QCStatus",
    "Uncertainty",
]
