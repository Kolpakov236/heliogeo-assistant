"""Правила сигналов (контракт rules/signals.yaml): паспорта признаков, полосы нормы, сигналы.

Конвейер: признаки (``features.compute``) -> правила этого контракта -> сигналы -> LLM, которая
объясняет, что сигналы значат. Контракт описывает три вещи.

* **Паспорт признака** (``features``): смысл, единица, допустимый диапазон, полосы нормы
  (``bands``) и что делать, если значения нет (``on_missing``).
* **Сигналы** (``signals``): условие над признаками -> сигнал с важностью и пояснением. Условия —
  структурные данные (``feature`` + оператор, ``all``/``any``/``not``), а не текст: их проверяет
  схема, и разбирающему коду не нужен ``eval``.
* **Итоговые сигналы** (``kind: summary``): «всё в норме» и «спокойно по неполным данным». Тишина
  не должна читаться как «не знаем»: если тревожных сигналов нет, отчёт всё равно выдаётся.

Пустое значение признака — не норма и не ноль. Для критичных признаков правило ``on_missing``
обязательно: оно называет сигнал «данных нет» (``kind: missing_data``). Признак, у которого
пустота законна (максимум скорости выбросов при нуле выбросов), объявляет это условием
``empty_ok_when``.

Проверки без обращения к другим контрактам — в моделях ниже; сверка с ``features/specs.yaml``
(существование признаков, типы и диапазоны порогов) — в ``check_signals``.
"""

import re
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from heliogeo.contracts.envelope import ContractModel
from heliogeo.contracts.features import FeaturesContract
from heliogeo.contracts.loader import contracts_root, load_model

SIGNALS_PATH = "rules/signals.yaml"

Scalar = bool | int | float | str
_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
_ID_RE = re.compile(_ID_PATTERN)
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Id = Annotated[str, StringConstraints(pattern=_ID_PATTERN)]


class FeatureType(StrEnum):
    """Тип значения признака (по нему проверяются пороги в условиях)."""

    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING = "string"

    @property
    def is_numeric(self) -> bool:
        return self in (FeatureType.INTEGER, FeatureType.NUMBER)


class Severity(StrEnum):
    """Важность сигнала по возрастанию."""

    INFO = "info"  # к сведению, на «всё в норме» не влияет
    NOTICE = "notice"  # заметное отклонение или нехватка данных
    WARNING = "warning"  # возможны последствия для систем
    ALERT = "alert"  # сильное воздействие

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.index(self)

    def at_least(self, other: "Severity") -> bool:
        return self.rank >= other.rank


_SEVERITY_ORDER = (Severity.INFO, Severity.NOTICE, Severity.WARNING, Severity.ALERT)


class SignalKind(StrEnum):
    """Вид сигнала."""

    SIGNAL = "signal"  # обычный: условие над признаками
    SUMMARY = "summary"  # итоговый: «всё в норме» / «спокойно по неполным данным»
    MISSING_DATA = "missing_data"  # «данных нет», запускается правилом on_missing


class System(StrEnum):
    """Системы, на которые сигнал влияет (или не влияет, в итоговом сигнале)."""

    HF_COM = "hf_com"  # коротковолновая радиосвязь
    GNSS = "gnss"  # спутниковая навигация
    SATCOM = "satcom"  # спутниковая связь
    RADIATION = "radiation"  # радиация на высоте полёта
    POWER_GRID = "power_grid"  # электросети


class Quality(StrEnum):
    """Качество признака, при котором правило допустимо применять (статусы compute_features)."""

    OK = "ok"
    DEGRADED = "degraded"


# --- Условия ---

_COMPARISONS = ("gt", "gte", "lt", "lte")
_OPERATORS = (*_COMPARISONS, "eq", "ne", "in_")
_COMBINATORS = ("all_", "any_", "not_")


class Condition(BaseModel):
    """Условие над признаками: лист (``feature`` и один оператор) или комбинатор.

    Лист: ``{feature: kp_max_1d, gte: 5}``; операторы ``gt``, ``gte``, ``lt``, ``lte``, ``eq``,
    ``ne``, ``in``. Комбинаторы: ``all`` и ``any`` (непустые списки), ``not``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    feature: _Id | None = None
    gt: float | None = None
    gte: float | None = None
    lt: float | None = None
    lte: float | None = None
    eq: Scalar | None = None
    ne: Scalar | None = None
    in_: tuple[Scalar, ...] | None = Field(default=None, alias="in")
    all_: tuple["Condition", ...] | None = Field(default=None, alias="all")
    any_: tuple["Condition", ...] | None = Field(default=None, alias="any")
    not_: "Condition | None" = Field(default=None, alias="not")

    @field_validator("gt", "gte", "lt", "lte", mode="before")
    @classmethod
    def _numbers_only(cls, value: Any) -> Any:
        # YAML читает «1e-5» как строку (нужно «1.0e-5»), а True как число: оба случая ошибка
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(
                f"порог должен быть числом (получено {value!r}; степени записываются как 1.0e-5)"
            )
        return value

    @model_validator(mode="after")
    def _check_form(self) -> "Condition":
        used = self.model_fields_set
        operators = [name for name in _OPERATORS if name in used]
        combinators = [name for name in _COMBINATORS if name in used]
        if self.feature is not None:
            if len(operators) != 1 or combinators:
                raise ValueError(
                    "лист условия содержит feature и ровно один оператор "
                    "(gt, gte, lt, lte, eq, ne, in), без all/any/not"
                )
            if self.in_ is not None and not self.in_:
                raise ValueError("оператор in требует непустой список")
            return self
        if operators or len(combinators) != 1:
            raise ValueError("условие без feature содержит ровно один из all, any, not")
        if (self.all_ is not None and not self.all_) or (self.any_ is not None and not self.any_):
            raise ValueError("all и any требуют непустой список условий")
        return self

    def leaves(self) -> Iterator["Condition"]:
        """Все листья условия (с ``feature``) в порядке записи."""
        if self.feature is not None:
            yield self
            return
        for child in (*(self.all_ or ()), *(self.any_ or ())):
            yield from child.leaves()
        if self.not_ is not None:
            yield from self.not_.leaves()

    def features(self) -> set[str]:
        return {leaf.feature for leaf in self.leaves() if leaf.feature is not None}

    def operator(self) -> tuple[str, Any]:
        """Оператор и значение листа: ('gte', 5.0), ('in', (...))."""
        for name in _OPERATORS:
            if name in self.model_fields_set:
                return name.rstrip("_"), getattr(self, name)
        raise ValueError("не лист условия")


class SummaryWhen(BaseModel):
    """Условие итогового сигнала: нет сигналов не ниже порога важности и состояние критичных данных.

    ``all_present`` — все критичные признаки есть; ``some_missing`` — часть критичных есть, а часть
    нет. Когда нет ни одного критичного признака, итогового сигнала нет: «спокойно по имеющимся
    данным» без данных было бы ложью, а сигналы «данных нет» и так объясняют ситуацию.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    no_signals_at_least: Severity
    critical: Literal["all_present", "some_missing"]


# --- Паспорт признака ---


class Band(BaseModel):
    """Полоса значений признака.

    Полосы идут по возрастанию: верхняя граница ``lt`` исключена, нижняя — верхняя граница
    предыдущей (у первой — начало ``valid_range``), у последней ``lt`` нет (до конца диапазона).
    Поэтому дыр и пересечений между полосами быть не может.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: _Id
    explain: _Text
    lt: float | None = None


class OnMissing(BaseModel):
    """Что делать, если значения признака нет."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal: _Id


class FeatureProfile(BaseModel):
    """Паспорт признака: смысл и ограничения."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: FeatureType
    meaning: _Text
    unit: _Text | None = None
    valid_range: tuple[float, float] | None = None
    allowed_values: tuple[str, ...] | None = None
    critical: bool = False  # без него нельзя говорить «всё в норме»
    bands: tuple[Band, ...] = ()
    on_missing: OnMissing | None = None
    empty_ok_when: Condition | None = None  # когда пустое значение законно (нет событий)
    llm_note: _Text | None = None

    @model_validator(mode="after")
    def _check_profile(self) -> "FeatureProfile":
        if self.valid_range is not None:
            if not self.type.is_numeric:
                raise ValueError("valid_range только у числовых признаков")
            low, high = self.valid_range
            if not low < high:
                raise ValueError(
                    f"valid_range: левая граница должна быть меньше правой ({low}, {high})"
                )
        if self.allowed_values is not None:
            if self.type is not FeatureType.STRING:
                raise ValueError("allowed_values только у строковых признаков")
            if len(set(self.allowed_values)) != len(self.allowed_values):
                raise ValueError("allowed_values содержит повторы")
        if self.critical and self.on_missing is None:
            raise ValueError("у критичного признака обязателен on_missing")
        if self.on_missing is not None and self.empty_ok_when is not None:
            raise ValueError("on_missing и empty_ok_when взаимоисключающие")
        self._check_bands()
        return self

    def _check_bands(self) -> None:
        if not self.bands:
            return
        if not self.type.is_numeric or self.valid_range is None:
            raise ValueError("полосы bands требуют числового признака с valid_range")
        low, high = self.valid_range
        labels = [band.label for band in self.bands]
        if len(set(labels)) != len(labels):
            raise ValueError("метки полос повторяются")
        bounds = [band.lt for band in self.bands]
        if bounds[-1] is not None:
            raise ValueError("у последней полосы нет lt: она идёт до конца диапазона")
        edges = [bound for bound in bounds[:-1] if bound is not None]
        if len(edges) != len(bounds) - 1:
            raise ValueError("lt обязателен у всех полос, кроме последней")
        previous = low
        for edge in edges:
            if not previous < edge <= high:
                raise ValueError(
                    f"границы полос должны строго возрастать внутри valid_range ({low}, {high}); "
                    f"получено lt={edge} после {previous}"
                )
            previous = edge


# --- Сигналы ---


class SignalRule(BaseModel):
    """Правило сигнала."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: _Id
    title: _Text | None = None
    severity: Severity
    kind: SignalKind = SignalKind.SIGNAL
    when: Condition | SummaryWhen | None = None
    requires_quality: tuple[Quality, ...] = (Quality.OK, Quality.DEGRADED)
    explain: _Text
    affects: tuple[System, ...] = ()  # на что влияет
    unaffected: tuple[System, ...] = ()  # на что не влияет (итоговые сигналы)
    forbids_claims: tuple[_Id, ...] = ()  # утверждения, которые текст не вправе делать

    @model_validator(mode="after")
    def _check_kind(self) -> "SignalRule":
        if self.kind is SignalKind.SIGNAL and not isinstance(self.when, Condition):
            raise ValueError(
                "обычный сигнал требует условия when (feature + оператор или all/any/not)"
            )
        if self.kind is SignalKind.SUMMARY and not isinstance(self.when, SummaryWhen):
            raise ValueError("итоговый сигнал требует when: {no_signals_at_least, critical}")
        if self.kind is SignalKind.MISSING_DATA and self.when is not None:
            raise ValueError("сигнал missing_data запускается правилом on_missing и when не имеет")
        if not self.requires_quality:
            raise ValueError("requires_quality не может быть пустым")
        if self.unaffected and self.kind is not SignalKind.SUMMARY:
            raise ValueError("unaffected только у итоговых сигналов")
        return self

    def summary_mode(self) -> str | None:
        """``all_present`` / ``some_missing`` у итогового сигнала, иначе ``None``."""
        if self.kind is SignalKind.SUMMARY and isinstance(self.when, SummaryWhen):
            return self.when.critical
        return None


class SignalsContract(ContractModel):
    """Контракт rules/signals.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    features: dict[str, FeatureProfile] = Field(min_length=1)
    signals: tuple[SignalRule, ...] = Field(min_length=1)

    @field_validator("features")
    @classmethod
    def _check_feature_ids(cls, features: dict[str, FeatureProfile]) -> dict[str, FeatureProfile]:
        bad = [name for name in features if not _ID_RE.fullmatch(name)]
        if bad:
            raise ValueError(f"недопустимые id признаков: {', '.join(bad)}")
        return features

    @model_validator(mode="after")
    def _check_signals(self) -> "SignalsContract":
        ids = [rule.id for rule in self.signals]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"повторяющиеся id сигналов: {', '.join(duplicates)}")

        by_id = {rule.id: rule for rule in self.signals}
        for name, profile in self.features.items():
            if profile.on_missing is None:
                continue
            target = by_id.get(profile.on_missing.signal)
            if target is None:
                raise ValueError(
                    f"features.{name}.on_missing: сигнала {profile.on_missing.signal!r} "
                    "нет в signals"
                )
            if target.kind is not SignalKind.MISSING_DATA:
                raise ValueError(
                    f"features.{name}.on_missing: сигнал {target.id!r} "
                    "должен иметь kind: missing_data"
                )

        all_present = [r for r in self.signals if r.summary_mode() == "all_present"]
        some_missing = [r for r in self.signals if r.summary_mode() == "some_missing"]
        if len(all_present) != 1:
            raise ValueError(
                "нужен ровно один итоговый сигнал «всё в норме» "
                f"(kind: summary, critical: all_present); найдено {len(all_present)}"
            )
        if len(some_missing) > 1:
            raise ValueError("итоговый сигнал critical: some_missing может быть только один")
        return self

    def critical_features(self) -> tuple[str, ...]:
        """Критичные признаки: без них нельзя говорить «всё в норме»."""
        return tuple(name for name, profile in self.features.items() if profile.critical)


def load_signals(root: Path | None = None) -> SignalsContract:
    """Прочитать и проверить contracts/rules/signals.yaml."""
    return load_model(contracts_root(root) / SIGNALS_PATH, SignalsContract)


# --- Сверка с контрактом признаков ---


def check_signals(signals: SignalsContract, features: FeaturesContract) -> list[str]:
    """Сверить правила с ``features/specs.yaml``; возвращает список проблем (пустой — порядок).

    Проверяется: паспорта есть ровно у тех признаков, что объявлены в specs; признаки в условиях
    существуют; типы и значения порогов подходят признаку (число — число, значение строки — из
    allowed_values, порог лежит внутри valid_range).
    """
    spec_ids = set(features.by_id())
    profile_ids = set(signals.features)
    problems: list[str] = []

    for missing in sorted(spec_ids - profile_ids):
        problems.append(f"{SIGNALS_PATH}: нет паспорта признака {missing!r} из features/specs.yaml")
    for unknown in sorted(profile_ids - spec_ids):
        problems.append(f"{SIGNALS_PATH}: паспорт признака {unknown!r}, которого нет в specs.yaml")

    for name, profile in signals.features.items():
        if profile.empty_ok_when is not None:
            problems.extend(
                _check_condition(profile.empty_ok_when, signals, f"features.{name}.empty_ok_when")
            )
    for rule in signals.signals:
        if isinstance(rule.when, Condition):
            problems.extend(_check_condition(rule.when, signals, f"signals.{rule.id}.when"))
    return problems


def _check_condition(condition: Condition, signals: SignalsContract, where: str) -> list[str]:
    problems: list[str] = []
    for leaf in condition.leaves():
        assert leaf.feature is not None
        profile = signals.features.get(leaf.feature)
        if profile is None:
            problems.append(f"{where}: у признака {leaf.feature!r} нет паспорта")
            continue
        operator, value = leaf.operator()
        values = value if operator == "in" else (value,)
        for item in values:
            problems.extend(_check_value(profile, leaf.feature, operator, item, where))
    return problems


def _check_value(
    profile: FeatureProfile, feature: str, operator: str, value: Scalar, where: str
) -> list[str]:
    prefix = f"{where}: {feature} {operator} {value!r}"
    kind = profile.type
    if operator in _COMPARISONS and not kind.is_numeric:
        return [f"{prefix}: сравнение «больше/меньше» только для числовых признаков"]
    if kind.is_numeric:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return [f"{prefix}: для числового признака нужен числовой порог"]
        if kind is FeatureType.INTEGER and float(value) != int(value):
            return [f"{prefix}: у целого признака порог должен быть целым"]
        if profile.valid_range is not None:
            low, high = profile.valid_range
            if not low <= value <= high:
                return [f"{prefix}: порог вне допустимого диапазона [{low}, {high}]"]
        return []
    if kind is FeatureType.BOOLEAN:
        return (
            []
            if isinstance(value, bool)
            else [f"{prefix}: у логического признака нужен true/false"]
        )
    if not isinstance(value, str):
        return [f"{prefix}: у строкового признака нужна строка"]
    if profile.allowed_values is not None and value not in profile.allowed_values:
        return [f"{prefix}: значение вне allowed_values {list(profile.allowed_values)}"]
    return []
