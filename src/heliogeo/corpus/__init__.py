"""Корпус эталонных текстов NOAA: индекс выпусков и быстрый доступ к ним.

Тексты (Forecast Discussion 00:30 и 12:30, RSGA 22:00) скачиваются в сырой слой бэкфиллом
(``heliogeo.functions.backfill``), из них один раз собирается индекс (``build_corpus``), и дальше
``Corpus`` отдаёт выпуски, разделы, предложения и пропуски в архиве из памяти. Корпус нужен для
эталонной генерации: подбора примеров под обстановку, частотного словаря оборотов, пар «данные →
текст» с правильно выровненными окнами (``TextRecord.data_days``).
"""

from heliogeo.corpus.index import (
    EXPECTED_ISSUES,
    INDEX_NAME,
    BuildReport,
    Corpus,
    build_corpus,
)
from heliogeo.corpus.records import CorpusError, TextRecord, split_sentences

__all__ = [
    "EXPECTED_ISSUES",
    "INDEX_NAME",
    "BuildReport",
    "Corpus",
    "CorpusError",
    "TextRecord",
    "build_corpus",
    "split_sentences",
]
