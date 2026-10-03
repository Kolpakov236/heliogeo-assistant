"""Модель факта: значение с происхождением, качеством, окном валидности и политикой отображения.

Факт — единственная форма, в которой значение попадает в планировщик, генерацию и проверку.
Число мимо фактов не берётся: иначе в одном документе появятся два разных округления одной
величины. Отсутствие данных выражается фактом со статусом ``missing``, а не голым ``None``:
так не теряется информация о том, что данные запрашивались.

Все объекты неизменяемы и создаются сразу проверенными: нарушение инварианта даёт
``ValueError`` при создании, а не ошибку в готовом выпуске. Описание и примеры — в docs/facts.md.
"""

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from string import Formatter
from types import MappingProxyType
from typing import Any, Self, TypeVar

from heliogeo.facts.formatting import (
    DEFAULT_LANG,
    FORMATTERS,
    LANGUAGES,
    Lang,
    check_lang,
    format_number,
)

FACT_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
"""Идентификатор факта: не меньше двух сегментов через точку, строчные латинские буквы, цифры, «_»."""

# Допуск на сумму вероятностей распределения: значения могут быть округлены до трёх знаков.
_DISTRIBUTION_TOLERANCE = 5e-3

# Поля, разрешённые в шаблоне утверждения: {value} — результат display_value, {unit} — единица.
_TEMPLATE_FIELDS = frozenset({"value", "unit"})

# Слова для boolean по умолчанию: (для true, для false). Переопределяются в value_map.
_BOOLEAN_WORDS: Mapping[Lang, tuple[str, str]] = {"ru": ("да", "нет"), "en": ("yes", "no")}
_INTERVAL_TEXT: Mapping[Lang, str] = {"ru": "от {low} до {high}", "en": "from {low} to {high}"}
_TIME_RANGE_TEXT: Mapping[Lang, str] = {"ru": "с {low} по {high}", "en": "from {low} to {high}"}

_E = TypeVar("_E", bound=StrEnum)


class FactKind(StrEnum):
    """Вид значения факта. От него зависит форма ``Fact.value``."""

    SCALAR = "scalar"  # число: 449.6 км/с
    INTERVAL = "interval"  # пара чисел (low, high): (347.9, 449.6)
    CATEGORICAL = "categorical"  # строка из конечного набора: "Dai", "away", "G1"
    BOOLEAN = "boolean"  # True / False
    COUNT = "count"  # целое не меньше нуля: 6 областей
    LIST = "list"  # кортеж строк, чисел или bool: номера областей, классы вспышек
    TIMESTAMP = "timestamp"  # момент времени (UTC): пик вспышки
    TIME_RANGE = "time_range"  # пара моментов (начало, конец): окно прихода выброса
    DISTRIBUTION = "distribution"  # пары (метка, вероятность), сумма 1: прогноз по уровням


class QCStatus(StrEnum):
    """Статус качества значения."""

    OK = "ok"  # прямое измерение, свежее
    DEGRADED = "degraded"  # реконструкция, суррогат или низкая уверенность
    STALE = "stale"  # устарело относительно допустимого срока
    MISSING = "missing"  # данных нет (value is None)
    INVALID = "invalid"  # значение вне допустимого диапазона


# --- Проверки и преобразования ---


def _check_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: нужна непустая строка, получено {value!r}")
    return value


def _check_optional_text(value: object, name: str) -> str | None:
    return None if value is None else _check_text(value, name)


def _check_number(value: object, name: str) -> float:
    """Конечное число (int или float, но не bool) как float."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name}: нужно конечное число, получено {value!r}")
    return float(value)


def _to_utc(value: object, name: str) -> datetime:
    """Момент времени с часовым поясом, приведённый к UTC. Наивное время запрещено."""
    if not isinstance(value, datetime):
        raise ValueError(f"{name}: нужен datetime, получено {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}: нужен datetime с часовым поясом, наивное время запрещено")
    return value.astimezone(UTC)


def _parse_datetime(value: object, name: str) -> datetime:
    """Время из ISO 8601 (например, ``2026-09-01T12:00:00+00:00`` или ``2026-09-01T21:24Z``)."""
    if not isinstance(value, str):
        raise ValueError(f"{name}: нужна строка ISO 8601, получено {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name}: не время ISO 8601: {value!r}") from exc
    return _to_utc(parsed, name)


def _parse_enum(enum_type: type[_E], raw: object, name: str) -> _E:
    """Член перечисления по члену или по его строковому значению; иначе ``ValueError``."""
    if isinstance(raw, enum_type):
        return raw
    if isinstance(raw, str):
        try:
            return enum_type(raw)
        except ValueError:
            pass
    allowed = ", ".join(member.value for member in enum_type)
    raise ValueError(f"{name}: неизвестное значение {raw!r}; допустимы: {allowed}")


def _check_keys(
    data: object, *, name: str, required: Collection[str], optional: Collection[str]
) -> Mapping[str, Any]:
    """Словарь с известными ключами: нет лишних и не хватает обязательных — ``ValueError``."""
    if not isinstance(data, Mapping):
        raise ValueError(f"{name}: нужен словарь, получено {type(data).__name__}")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"{name}: нет обязательных полей: {', '.join(missing)}")
    allowed = {*required, *optional}
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ValueError(f"{name}: неизвестные поля: {', '.join(unknown)}")
    return data


def _language_texts(value: object, name: str) -> Mapping[str, str]:
    """Словарь «язык: текст», копия только для чтения с отсортированными ключами."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{name}: нужен словарь «язык: текст», получено {type(value).__name__}")
    result: dict[str, str] = {}
    for lang, text in value.items():
        if lang not in LANGUAGES:
            raise ValueError(f"{name}: неизвестный язык {lang!r}; поддерживаются: {_languages()}")
        result[lang] = _check_text(text, f"{name}[{lang}]")
    return MappingProxyType(dict(sorted(result.items())))


def _value_maps(value: object, name: str) -> Mapping[str, Mapping[str, str]]:
    """Словарь «язык: {значение: слово}», копия только для чтения."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{name}: нужен словарь «язык: {{значение: слово}}»")
    result: dict[str, Mapping[str, str]] = {}
    for lang, words in value.items():
        if lang not in LANGUAGES:
            raise ValueError(f"{name}: неизвестный язык {lang!r}; поддерживаются: {_languages()}")
        if not isinstance(words, Mapping) or not words:
            raise ValueError(f"{name}[{lang}]: нужен непустой словарь «значение: слово»")
        checked = {
            _check_text(key, f"{name}[{lang}] ключ"): _check_text(word, f"{name}[{lang}][{key}]")
            for key, word in words.items()
        }
        result[lang] = MappingProxyType(dict(sorted(checked.items())))
    return MappingProxyType(dict(sorted(result.items())))


def _languages() -> str:
    return ", ".join(LANGUAGES)


def _template_fields(template: str, name: str) -> frozenset[str]:
    """Поля шаблона утверждения; допустимы только ``{value}`` и ``{unit}`` без формата."""
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(f"{name}: некорректный шаблон ({exc})") from exc
    names: set[str] = set()
    for _literal, field_name, spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in _TEMPLATE_FIELDS or spec or conversion:
            raise ValueError(
                f"{name}: в шаблоне допустимы только поля {{value}} и {{unit}} без формата, "
                f"найдено {field_name!r}"
            )
        names.add(field_name)
    return frozenset(names)


def _freeze_for_hash(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple((key, _freeze_for_hash(item)) for key, item in sorted(value.items()))
    return value


# --- Значения по видам ---


def _pair(value: object, name: str) -> tuple[Any, Any]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence) or len(value) != 2:
        raise ValueError(f"{name}: нужна пара значений, получено {value!r}")
    return value[0], value[1]


def _list_item(item: object, index: int) -> str | int | float | bool:
    name = f"value[{index}]"
    if isinstance(item, bool | int):
        return item
    if isinstance(item, str):
        return _check_text(item, name)
    if isinstance(item, float) and math.isfinite(item):
        return item
    raise ValueError(f"{name}: элемент списка — строка, конечное число или bool: {item!r}")


def _normalize_distribution(value: object) -> tuple[tuple[str, float], ...]:
    """Распределение как кортеж пар (метка, вероятность) в порядке следования."""
    entries: list[tuple[Any, Any]]
    if isinstance(value, Mapping):
        entries = list(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        entries = [_pair(entry, f"value[{index}]") for index, entry in enumerate(value)]
    else:
        raise ValueError("value: для distribution нужен словарь {метка: вероятность} или пары")
    if not entries:
        raise ValueError("value: распределение не может быть пустым")
    pairs = tuple(
        (_check_text(label, f"value[{index}] метка"), _check_number(prob, f"value[{label!r}]"))
        for index, (label, prob) in enumerate(entries)
    )
    labels = [label for label, _ in pairs]
    if len(set(labels)) != len(labels):
        raise ValueError("value: метки распределения не должны повторяться")
    for label, prob in pairs:
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"value[{label!r}]: вероятность вне [0, 1]: {prob!r}")
    total = sum(prob for _, prob in pairs)
    if abs(total - 1.0) > _DISTRIBUTION_TOLERANCE:
        raise ValueError(f"value: сумма вероятностей должна быть 1, получено {total!r}")
    return pairs


def _normalize_value(kind: FactKind, value: object) -> Any:
    """Привести значение к неизменяемой канонической форме вида ``kind``.

    ``ValueError``, если форма значения не соответствует виду.
    """
    if value is None:
        return None
    match kind:
        case FactKind.SCALAR:
            return _check_number(value, "value")
        case FactKind.COUNT:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"value: для count нужно целое не меньше 0, получено {value!r}")
            return value
        case FactKind.BOOLEAN:
            if not isinstance(value, bool):
                raise ValueError(f"value: для boolean нужен bool, получено {value!r}")
            return value
        case FactKind.CATEGORICAL:
            return _check_text(value, "value")
        case FactKind.INTERVAL:
            raw_low, raw_high = _pair(value, "value")
            low = _check_number(raw_low, "value[0]")
            high = _check_number(raw_high, "value[1]")
            if low > high:
                raise ValueError(f"value: начало интервала больше конца: {value!r}")
            return (low, high)
        case FactKind.LIST:
            if isinstance(value, str | bytes) or not isinstance(value, Sequence):
                raise ValueError(f"value: для list нужна последовательность, получено {value!r}")
            return tuple(_list_item(item, index) for index, item in enumerate(value))
        case FactKind.TIMESTAMP:
            return _to_utc(value, "value")
        case FactKind.TIME_RANGE:
            raw_start, raw_end = _pair(value, "value")
            start = _to_utc(raw_start, "value[0]")
            end = _to_utc(raw_end, "value[1]")
            if start > end:
                raise ValueError(f"value: начало интервала времени позже конца: {value!r}")
            return (start, end)
        case FactKind.DISTRIBUTION:
            return _normalize_distribution(value)


def _encode_value(kind: FactKind, value: Any) -> Any:
    """Значение в форме, пригодной для JSON (значение уже нормализовано)."""
    if value is None:
        return None
    match kind:
        case FactKind.TIMESTAMP:
            return value.isoformat()
        case FactKind.TIME_RANGE:
            return [moment.isoformat() for moment in value]
        case FactKind.INTERVAL | FactKind.LIST:
            return list(value)
        case FactKind.DISTRIBUTION:
            return [[label, prob] for label, prob in value]
        case _:
            return value


def _decode_value(kind: FactKind, raw: Any) -> Any:
    """Обратное к ``_encode_value``: из JSON-формы в сырое значение (его нормализует ``Fact``)."""
    if raw is None:
        return None
    match kind:
        case FactKind.TIMESTAMP:
            return _parse_datetime(raw, "value")
        case FactKind.TIME_RANGE:
            first, second = _pair(raw, "value")
            return (_parse_datetime(first, "value[0]"), _parse_datetime(second, "value[1]"))
        case _:
            return raw


# --- Происхождение, неопределённость, политика отображения ---


@dataclass(frozen=True, kw_only=True)
class Provenance:
    """Откуда взято значение и как получено.

    Attributes:
        source: источник (например, ``swx_api:/data/2026-09-01#solar_wind.speed.max``).
        method: способ получения (``direct``, ``max over window``, ``count(...)``).
        files: файлы источника, из которых получено значение (``sources[].path`` ответа).
        fetched: когда ответ источника был получен (UTC).
        fingerprint: отпечаток прибора и спутника на момент измерения, например ``G18|omni``.
        note: пояснение.
    """

    source: str
    method: str
    files: tuple[str, ...] = ()
    fetched: datetime | None = None
    fingerprint: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _check_text(self.source, "provenance.source")
        _check_text(self.method, "provenance.method")
        if isinstance(self.files, str | bytes) or not isinstance(self.files, Sequence):
            raise ValueError(f"provenance.files: нужна последовательность строк: {self.files!r}")
        object.__setattr__(
            self,
            "files",
            tuple(_check_text(path, f"provenance.files[{i}]") for i, path in enumerate(self.files)),
        )
        if self.fetched is not None:
            object.__setattr__(self, "fetched", _to_utc(self.fetched, "provenance.fetched"))
        _check_optional_text(self.fingerprint, "provenance.fingerprint")
        _check_optional_text(self.note, "provenance.note")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "method": self.method,
            "files": list(self.files),
            "fetched": None if self.fetched is None else self.fetched.isoformat(),
            "fingerprint": self.fingerprint,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = _check_keys(
            data,
            name="provenance",
            required=("source", "method"),
            optional=("files", "fetched", "fingerprint", "note"),
        )
        fetched = data.get("fetched")
        return cls(
            source=data["source"],
            method=data["method"],
            files=data.get("files", ()),
            fetched=None if fetched is None else _parse_datetime(fetched, "provenance.fetched"),
            fingerprint=data.get("fingerprint"),
            note=data.get("note"),
        )


@dataclass(frozen=True, kw_only=True)
class Uncertainty:
    """Насколько можно доверять значению.

    Attributes:
        confidence: уверенность в значении, от 0 до 1 (для измерения 1.0).
        sigma: абсолютная погрешность (1σ) в единицах значения; для аддитивных возмущений.
        log_sigma: относительная погрешность в логарифмической шкале; для потоков частиц и
            рентгеновских потоков, где шум мультипликативный.
        note: пояснение, например «1 из 3 выбросов с неопределённым направлением».
    """

    confidence: float = 1.0
    sigma: float | None = None
    log_sigma: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        confidence = _check_number(self.confidence, "uncertainty.confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"uncertainty.confidence: нужно число от 0 до 1, получено {confidence}"
            )
        object.__setattr__(self, "confidence", confidence)
        for name in ("sigma", "log_sigma"):
            raw = getattr(self, name)
            if raw is None:
                continue
            number = _check_number(raw, f"uncertainty.{name}")
            if number < 0:
                raise ValueError(f"uncertainty.{name}: погрешность не может быть отрицательной")
            object.__setattr__(self, name, number)
        _check_optional_text(self.note, "uncertainty.note")

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "sigma": self.sigma,
            "log_sigma": self.log_sigma,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = _check_keys(
            data,
            name="uncertainty",
            required=(),
            optional=("confidence", "sigma", "log_sigma", "note"),
        )
        return cls(
            confidence=data.get("confidence", 1.0),
            sigma=data.get("sigma"),
            log_sigma=data.get("log_sigma"),
            note=data.get("note"),
        )


@dataclass(frozen=True, kw_only=True)
class DisplayPolicy:
    """Как значение превращается в текст. Единственное место, где число становится строкой.

    Attributes:
        round_to: шаг округления: 25 для скорости ветра, 1 для поля, 5 для Dst. Округление
            грубое намеренно: оно отражает реальную точность величины.
        unit_text: единица для текста по языкам: ``{"ru": "км/с", "en": "km/s"}``.
        formatter: имя именованного форматтера (``kp``: 2.333 даёт «2+»). Не сочетается с
            ``round_to``.
        value_map: слова вместо значений по языкам: ``{"ru": {"G1": "слабая"}}`` для categorical;
            для boolean ключи ``"true"`` и ``"false"``.
        statement: шаблон канонического утверждения по языкам; поля ``{value}`` и ``{unit}``.
    """

    round_to: float | None = None
    unit_text: Mapping[str, str] = field(default_factory=dict)
    formatter: str | None = None
    value_map: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    statement: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.round_to is not None:
            step = _check_number(self.round_to, "display.round_to")
            if step <= 0:
                raise ValueError("display.round_to: шаг округления должен быть положительным")
            object.__setattr__(self, "round_to", step)
        if self.formatter is not None:
            if self.formatter not in FORMATTERS:
                raise ValueError(
                    f"display.formatter: неизвестный форматтер {self.formatter!r}; "
                    f"допустимы: {', '.join(sorted(FORMATTERS))}"
                )
            if self.round_to is not None:
                raise ValueError("display: round_to и formatter взаимно исключают друг друга")
        unit_text = _language_texts(self.unit_text, "display.unit_text")
        statement = _language_texts(self.statement, "display.statement")
        for lang, template in statement.items():
            names = _template_fields(template, f"display.statement[{lang}]")
            if "value" not in names:
                raise ValueError(f"display.statement[{lang}]: в шаблоне нет поля {{value}}")
            if "unit" in names and lang not in unit_text:
                raise ValueError(
                    f"display.statement[{lang}]: шаблон использует {{unit}}, "
                    f"а display.unit_text[{lang}] не задан"
                )
        object.__setattr__(self, "unit_text", unit_text)
        object.__setattr__(self, "statement", statement)
        object.__setattr__(self, "value_map", _value_maps(self.value_map, "display.value_map"))

    def __hash__(self) -> int:
        return hash(
            (
                self.round_to,
                self.formatter,
                _freeze_for_hash(self.unit_text),
                _freeze_for_hash(self.value_map),
                _freeze_for_hash(self.statement),
            )
        )

    def __reduce__(self) -> tuple[Any, ...]:
        # словари только для чтения (MappingProxyType) не копируются и не сериализуются сами
        return (type(self).from_dict, (self.to_dict(),))

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_to": self.round_to,
            "unit_text": dict(self.unit_text),
            "formatter": self.formatter,
            "value_map": {lang: dict(words) for lang, words in self.value_map.items()},
            "statement": dict(self.statement),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        data = _check_keys(
            data,
            name="display",
            required=(),
            optional=("round_to", "unit_text", "formatter", "value_map", "statement"),
        )
        return cls(
            round_to=data.get("round_to"),
            unit_text=data.get("unit_text", {}),
            formatter=data.get("formatter"),
            value_map=data.get("value_map", {}),
            statement=data.get("statement", {}),
        )


# --- Факт ---


@dataclass(frozen=True, kw_only=True)
class Fact:
    """Факт: значение вместе с происхождением, качеством, окном валидности и политикой отображения.

    Все поля именованные. Объект неизменяем; при создании значение приводится к канонической
    форме вида ``kind`` (числа — float, последовательности — кортежи, время — UTC), поэтому факты
    сравниваются и хэшируются по значению.

    Attributes:
        id: идентификатор вида ``домен.имя`` (``sw.speed.max_24h``).
        kind: вид значения.
        value: значение; ``None`` только если данных нет или они не определены.
        unit: единица измерения.
        t_valid_from: начало окна, к которому относится значение (UTC).
        t_valid_to: конец окна (UTC).
        provenance: происхождение.
        uncertainty: степень доверия.
        qc: статус качества.
        display: политика отображения.
        must_mention: факт обязательно должен быть упомянут в тексте.

    Инварианты: ``MISSING`` означает ``value is None``; ``OK`` означает ``value is not None``.
    Остальные статусы (``DEGRADED``, ``STALE``, ``INVALID``) допускают и то и другое.
    """

    id: str
    kind: FactKind
    value: Any | None
    unit: str | None = None
    t_valid_from: datetime
    t_valid_to: datetime
    provenance: Provenance
    uncertainty: Uncertainty = field(default_factory=Uncertainty)
    qc: QCStatus
    display: DisplayPolicy = field(default_factory=DisplayPolicy)
    must_mention: bool = False

    def __post_init__(self) -> None:
        try:
            self._validate()
        except ValueError as exc:
            raise ValueError(f"Факт {self.id!r}: {exc}") from exc

    def _validate(self) -> None:
        if not isinstance(self.id, str) or FACT_ID_PATTERN.fullmatch(self.id) is None:
            raise ValueError(
                "идентификатор должен быть вида «домен.имя»: строчные латинские буквы, цифры и "
                "«_», сегменты через точку"
            )
        kind = _parse_enum(FactKind, self.kind, "kind")
        qc = _parse_enum(QCStatus, self.qc, "qc")
        unit = _check_optional_text(self.unit, "unit")
        valid_from = _to_utc(self.t_valid_from, "t_valid_from")
        valid_to = _to_utc(self.t_valid_to, "t_valid_to")
        if valid_from > valid_to:
            raise ValueError("t_valid_from позже t_valid_to")
        for name, expected in (
            ("provenance", Provenance),
            ("uncertainty", Uncertainty),
            ("display", DisplayPolicy),
        ):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"{name}: нужен {expected.__name__}")
        if not isinstance(self.must_mention, bool):
            raise ValueError(f"must_mention: нужен bool, получено {self.must_mention!r}")
        value = _normalize_value(kind, self.value)
        if qc is QCStatus.MISSING and value is not None:
            raise ValueError("статус missing означает отсутствие значения, а значение задано")
        if qc is QCStatus.OK and value is None:
            raise ValueError("статус ok требует значения: для отсутствия данных нужен missing")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "qc", qc)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "t_valid_from", valid_from)
        object.__setattr__(self, "t_valid_to", valid_to)
        object.__setattr__(self, "value", value)

    def is_usable(self) -> bool:
        """Можно ли использовать значение в тексте: статус ok или degraded и значение есть."""
        return self.qc in (QCStatus.OK, QCStatus.DEGRADED) and self.value is not None

    def with_value(self, value: Any | None, *, qc: QCStatus | None = None) -> Self:
        """Копия факта с другим значением (и, при необходимости, статусом качества).

        Для проверки устойчивости к возмущениям данных и для пересчёта факта. Инварианты
        проверяются заново: заменить значение на ``None`` у факта со статусом ``ok`` нельзя,
        нужно передать ``qc=QCStatus.MISSING``.
        """
        return replace(self, value=value, qc=self.qc if qc is None else qc)

    def display_value(self, lang: Lang = DEFAULT_LANG) -> str:
        """Значение в виде строки по политике отображения.

        Единственное место, где число превращается в текст, поэтому у одной величины не бывает
        двух округлений. ``ValueError``, если значения нет (проверяйте ``is_usable``), если для
        числа не задана политика (``display.round_to`` или ``display.formatter``) или если
        значение категории не описано в ``display.value_map``. Отображение распределения пока
        не определено (``NotImplementedError``): используйте ``value``.
        """
        lang = check_lang(lang)
        value = self._require_value()
        match self.kind:
            case FactKind.SCALAR:
                return self._number_text(value, lang)
            case FactKind.COUNT:
                return str(value)
            case FactKind.BOOLEAN:
                return self._boolean_text(value, lang)
            case FactKind.CATEGORICAL:
                return self._categorical_text(value, lang)
            case FactKind.INTERVAL:
                return _INTERVAL_TEXT[lang].format(
                    low=self._number_text(value[0], lang), high=self._number_text(value[1], lang)
                )
            case FactKind.TIMESTAMP:
                return _moment_text(value)
            case FactKind.TIME_RANGE:
                return _TIME_RANGE_TEXT[lang].format(
                    low=_moment_text(value[0]), high=_moment_text(value[1])
                )
            case FactKind.LIST:
                if not value:
                    raise ValueError(
                        f"Факт {self.id!r}: пустой список не имеет отображения, "
                        "для «ничего нет» используйте count"
                    )
                return ", ".join(self._list_item_text(item, lang) for item in value)
            case FactKind.DISTRIBUTION:
                raise NotImplementedError(
                    f"Факт {self.id!r}: отображение распределения не определено, "
                    "используйте value"
                )

    def canonical_statement(self, lang: Lang = DEFAULT_LANG) -> str:
        """Факт в виде простого утвердительного предложения по шаблону ``display.statement``.

        Шаблон подставляет ``{value}`` (результат ``display_value``) и ``{unit}`` (из
        ``display.unit_text``). Текст не придумывается: нет шаблона для языка — ``ValueError``;
        нет значения — тоже ``ValueError`` (сначала проверьте ``is_usable``).
        """
        lang = check_lang(lang)
        template = self.display.statement.get(lang)
        if template is None:
            raise ValueError(
                f"Факт {self.id!r}: нет шаблона утверждения для языка {lang!r} (display.statement)"
            )
        return template.format(
            value=self.display_value(lang), unit=self.display.unit_text.get(lang, "")
        )

    def to_dict(self) -> dict[str, Any]:
        """Факт в виде словаря, пригодного для ``json.dumps``; обратное — ``from_dict``.

        Цикл ``Fact.from_dict(json.loads(json.dumps(fact.to_dict()))) == fact`` проходит без
        потерь. Порядок ключей фиксирован, поэтому сериализация детерминирована.
        """
        return {
            "id": self.id,
            "kind": self.kind.value,
            "value": _encode_value(self.kind, self.value),
            "unit": self.unit,
            "t_valid_from": self.t_valid_from.isoformat(),
            "t_valid_to": self.t_valid_to.isoformat(),
            "provenance": self.provenance.to_dict(),
            "uncertainty": self.uncertainty.to_dict(),
            "qc": self.qc.value,
            "display": self.display.to_dict(),
            "must_mention": self.must_mention,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Факт из словаря ``to_dict``. Лишние ключи и неверные формы — ``ValueError``."""
        data = _check_keys(
            data,
            name="Факт",
            required=("id", "kind", "value", "t_valid_from", "t_valid_to", "provenance", "qc"),
            optional=("unit", "uncertainty", "display", "must_mention"),
        )
        kind = _parse_enum(FactKind, data["kind"], "kind")
        return cls(
            id=data["id"],
            kind=kind,
            value=_decode_value(kind, data["value"]),
            unit=data.get("unit"),
            t_valid_from=_parse_datetime(data["t_valid_from"], "t_valid_from"),
            t_valid_to=_parse_datetime(data["t_valid_to"], "t_valid_to"),
            provenance=Provenance.from_dict(data["provenance"]),
            uncertainty=Uncertainty.from_dict(data.get("uncertainty", {})),
            qc=_parse_enum(QCStatus, data["qc"], "qc"),
            display=DisplayPolicy.from_dict(data.get("display", {})),
            must_mention=data.get("must_mention", False),
        )

    # --- Отображение: детали ---

    def _require_value(self) -> Any:
        if self.value is None:
            raise ValueError(
                f"Факт {self.id!r} не имеет значения (qc={self.qc.value}): "
                "сначала проверьте is_usable()"
            )
        return self.value

    def _number_text(self, number: float, lang: Lang) -> str:
        policy = self.display
        if policy.formatter is not None:
            return FORMATTERS[policy.formatter](number)
        if policy.round_to is not None:
            return format_number(number, round_to=policy.round_to, lang=lang)
        raise ValueError(
            f"Факт {self.id!r}: не задана политика отображения числа "
            "(display.round_to или display.formatter)"
        )

    def _mapped_word(self, key: str, lang: Lang) -> str | None:
        """Слово из value_map для языка; ``None``, если для языка карты нет вовсе."""
        words = self.display.value_map.get(lang)
        if words is None:
            return None
        if key not in words:
            raise ValueError(
                f"Факт {self.id!r}: значения {key!r} нет в display.value_map[{lang}]; "
                f"описаны: {', '.join(words)}"
            )
        return words[key]

    def _boolean_text(self, value: bool, lang: Lang) -> str:
        word = self._mapped_word("true" if value else "false", lang)
        if word is not None:
            return word
        yes, no = _BOOLEAN_WORDS[lang]
        return yes if value else no

    def _categorical_text(self, value: str, lang: Lang) -> str:
        word = self._mapped_word(value, lang)
        return value if word is None else word

    def _list_item_text(self, item: str | int | float | bool, lang: Lang) -> str:
        if isinstance(item, bool):
            return self._boolean_text(item, lang)
        if isinstance(item, float):
            return self._number_text(item, lang)
        return str(item)


def _moment_text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")
