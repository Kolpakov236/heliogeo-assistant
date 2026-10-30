"""Запись корпуса: один выпуск текста NOAA (Forecast Discussion или RSGA) и разбор его разделов.

Разделы читаются из сырого тела ответа, а не из ``SwxTextResponse.sections``: типизированный
разбор понимает только разделы-словари (Discussion) и молча отбрасывает разделы RSGA, у которых
значение — просто строка. В записи оба вида приведены к одной форме: ``{раздел: {часть: текст}}``;
у Discussion части ``text``, ``24 hr Summary`` и ``Forecast``, у RSGA единственная часть ``text``.

Какой период описывает текст. Эталон описывает 24 часа, оканчивающиеся в момент выпуска (Discussion
00:30 за D говорит о сутках D−1, а не о D). Поэтому у записи есть ``covers_from``/``covers_to`` и
``data_days``: по ним подбираются данные для пары «данные → текст». Для RSGA 22:00 это такое же
приближение, а не точная граница.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Self

from heliogeo.extractors.swx.response import SwxTextResponse

COVERS = timedelta(hours=24)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


class CorpusError(Exception):
    """Запись корпуса не собирается из ответа или из строки индекса."""


@dataclass(frozen=True, kw_only=True)
class TextRecord:
    """Один выпуск текста.

    Attributes:
        issued_utc: момент выпуска (UTC, с часовым поясом).
        kind: ``discussion`` или ``rsga``.
        cycle: цикл выпуска, например ``0030``.
        covers_from: начало описываемого периода (``issued_utc`` минус 24 часа).
        covers_to: конец описываемого периода (``issued_utc``).
        sections: разделы ``{название: {часть: текст}}``.
        text: полный сырой текст продукта.
        raw_key: ключ тела ответа в сыром слое.
        raw_sha256: хэш тела ответа.
        source_path: файл источника, из которого взят текст.
        probabilities: вероятности RSGA как пришли (у Discussion ``None``).
        a_indices: A-индексы RSGA как пришли.
        f107: поток F10.7 из RSGA как пришёл.
        problems: что показалось неправильным при разборе (разбор не прерывается).
    """

    issued_utc: datetime
    kind: str
    cycle: str
    covers_from: datetime
    covers_to: datetime
    sections: Mapping[str, Mapping[str, str]]
    text: str
    raw_key: str
    raw_sha256: str
    source_path: str | None = None
    probabilities: Any = None
    a_indices: Any = None
    f107: Any = None
    problems: tuple[str, ...] = ()

    @property
    def day(self) -> date:
        """Сутки UTC, в которые выпущен текст."""
        return self.issued_utc.date()

    @property
    def data_days(self) -> tuple[date, ...]:
        """Сутки данных, которые нужны, чтобы покрыть описываемый период (одни или две)."""
        first, last = self.covers_from.date(), self.covers_to.date()
        return (first,) if first == last else (first, last)

    def section_text(self, name: str, part: str = "text") -> str | None:
        """Текст части раздела или ``None``, если такого раздела (части) нет."""
        return (self.sections.get(name) or {}).get(part)

    def to_json(self) -> dict[str, Any]:
        """Строка индекса: только JSON-типы, детерминированная."""
        return {
            "issued_utc": self.issued_utc.isoformat(),
            "kind": self.kind,
            "cycle": self.cycle,
            "covers_from": self.covers_from.isoformat(),
            "covers_to": self.covers_to.isoformat(),
            "sections": {name: dict(parts) for name, parts in self.sections.items()},
            "text": self.text,
            "raw_key": self.raw_key,
            "raw_sha256": self.raw_sha256,
            "source_path": self.source_path,
            "probabilities": self.probabilities,
            "a_indices": self.a_indices,
            "f107": self.f107,
            "problems": list(self.problems),
        }

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> Self:
        """Запись из строки индекса.

        Raises:
            CorpusError: если строка неполная или время не разбирается.
        """
        try:
            return cls(
                issued_utc=datetime.fromisoformat(row["issued_utc"]),
                kind=str(row["kind"]),
                cycle=str(row["cycle"]),
                covers_from=datetime.fromisoformat(row["covers_from"]),
                covers_to=datetime.fromisoformat(row["covers_to"]),
                sections={
                    str(name): {str(part): str(text) for part, text in parts.items()}
                    for name, parts in row["sections"].items()
                },
                text=str(row["text"]),
                raw_key=str(row["raw_key"]),
                raw_sha256=str(row["raw_sha256"]),
                source_path=row.get("source_path"),
                probabilities=row.get("probabilities"),
                a_indices=row.get("a_indices"),
                f107=row.get("f107"),
                problems=tuple(str(p) for p in row.get("problems") or ()),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CorpusError(f"строка индекса не разобрана: {exc!r}") from exc


def normalize_sections(value: Any) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Разделы из сырого тела приводятся к ``{раздел: {часть: текст}}``.

    Раздел-словарь (Discussion) сохраняет части; раздел-строка (RSGA) становится частью ``text``.
    Всё остальное пропускается с замечанием, а не роняет сборку.
    """
    if not isinstance(value, Mapping):
        return {}, [f"sections не объект: {type(value).__name__}"]
    sections: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for name, body in value.items():
        if isinstance(body, str):
            sections[str(name)] = {"text": body}
        elif isinstance(body, Mapping):
            sections[str(name)] = {str(part): str(text) for part, text in body.items()}
        else:
            problems.append(
                f"раздел {name!r}: ожидалась строка или объект, получено {type(body).__name__}"
            )
    return sections, problems


def record_from_response(response: SwxTextResponse, *, raw_key: str, raw_sha256: str) -> TextRecord:
    """Запись корпуса из типизированного ответа ``/text``.

    Args:
        response: разобранный ответ (его ``raw`` — тело как получено).
        raw_key: ключ тела в сыром слое.
        raw_sha256: хэш тела.

    Raises:
        CorpusError: если в ответе нет вида, цикла или времени выпуска.
    """
    if response.kind is None or response.cycle is None or response.issued_dt is None:
        raise CorpusError("в ответе нет kind, cycle или issued_dt: выпуск не опознан")
    sections, problems = normalize_sections(response.raw.get("sections"))
    # Типизированный разбор жалуется, что разделы RSGA — не объекты, и отбрасывает их; здесь они
    # читаются из сырого тела, поэтому эти замечания к записи не относятся.
    inherited = tuple(p for p in response.problems if not p.startswith("sections["))
    return TextRecord(
        issued_utc=response.issued_dt,
        kind=response.kind,
        cycle=response.cycle,
        covers_from=response.issued_dt - COVERS,
        covers_to=response.issued_dt,
        sections=sections,
        text=response.text or "",
        raw_key=raw_key,
        raw_sha256=raw_sha256,
        source_path=response.source.path if response.source is not None else None,
        probabilities=response.probabilities,
        a_indices=response.a_indices,
        f107=response.f107,
        problems=(*inherited, *problems),
    )


def split_sentences(text: str) -> list[str]:
    """Разбить текст на предложения.

    Переносы строк (тексты NOAA набраны в столбец) сворачиваются в пробел; граница — конец
    предложения и заглавная буква следующего. Числа с точкой («C2.6», «4.5 nT») и времена
    («31/1516 UTC») не разрываются.
    """
    flat = " ".join(text.split())
    return [sentence for sentence in _SENTENCE_END.split(flat) if sentence]
