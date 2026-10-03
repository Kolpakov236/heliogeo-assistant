"""Спецификации признаков для Feature Store (контракт features/specs.yaml, задача B3-01).

Каждый признак объявляет задержку доступности ``available_delay``: признак на момент t
считается только по данным с меткой времени не позже ``t - available_delay``, так Feature
Store не берёт данные из будущего (ЭТАП1 §2.7). Окна «за N суток» заканчиваются в этот же
момент. Версия файла (``version``) — это версия набора признаков ``feature_set_v``: она
записывается в строки признаков и прогнозов и повышается при любом изменении состава или
задержек.
"""

import re
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from heliogeo.contracts.envelope import ContractModel
from heliogeo.contracts.loader import contracts_root, load_model

FEATURES_PATH = "features/specs.yaml"

# Только дни и время (P1D, PT3H, PT1H30M, PT0S). Месяцы и годы не допускаются: их длина
# неоднозначна. «T» должен сопровождаться компонентой: «PT» и «P1DT» — не длительности.
_DELAY_RE = re.compile(
    r"P(?:(?P<days>[0-9]+)D)?"
    r"(?:T(?=[0-9])(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?(?:(?P<seconds>[0-9]+)S)?)?"
)


def parse_delay(value: object) -> timedelta:
    """Длительность ISO 8601 (только D/H/M/S) -> timedelta; отрицательной она быть не может."""
    if isinstance(value, timedelta):
        delay = value
    elif isinstance(value, str):
        match = _DELAY_RE.fullmatch(value)
        if match is None or not any(match.groupdict().values()):
            raise ValueError(
                f"задержка должна быть длительностью ISO 8601 вида PT3H, P1D, PT0S (получено {value!r})"
            )
        delay = timedelta(
            **{name: int(number) for name, number in match.groupdict().items() if number}
        )
    else:
        raise ValueError(
            f"задержка должна быть строкой ISO 8601, например PT3H (получено {value!r})"
        )
    if delay < timedelta(0):
        raise ValueError("задержка не может быть отрицательной")
    return delay


class FeatureGroup(StrEnum):
    """Группы признаков (ЭТАП1 §2.4)."""

    ACTIVITY_HISTORY = "activity_history"  # история активности: Kp, Dst
    RECURRENCE = "recurrence"  # рекуррентность 27 суток
    SOLAR_WIND = "solar_wind"  # солнечный ветер
    SOLAR_ACTIVITY = "solar_activity"  # активные области и вспышки
    CME = "cme"  # корональные выбросы массы
    CALENDAR = "calendar"  # календарь и фаза цикла
    EXTERNAL_FORECAST = "external_forecast"  # чужой прогноз (SWPC); модель без него — автономная


class FeatureStatus(StrEnum):
    """Состояние признака: объявлен или уже считается Feature Store."""

    PLANNED = "planned"
    ACTIVE = "active"


_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FeatureSpec(BaseModel):
    """Признак: что это, к какой группе относится и когда его данные становятся известны."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    group: FeatureGroup
    description: _Text
    # Обязателен и без значения по умолчанию: задержку нужно объявлять осознанно (ЭТАП1 §10, Д5)
    available_delay: Annotated[timedelta, BeforeValidator(parse_delay)]
    status: FeatureStatus = FeatureStatus.PLANNED
    note: _Text | None = None


class FeaturesContract(ContractModel):
    """Контракт features/specs.yaml: версия набора признаков и сами признаки."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    features: tuple[FeatureSpec, ...] = Field(min_length=1)

    @field_validator("features")
    @classmethod
    def _check_unique_ids(cls, features: tuple[FeatureSpec, ...]) -> tuple[FeatureSpec, ...]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for feature in features:
            if feature.id in seen and feature.id not in duplicates:
                duplicates.append(feature.id)
            seen.add(feature.id)
        if duplicates:
            raise ValueError(f"повторяющиеся id признаков: {', '.join(duplicates)}")
        return features

    def active(self) -> tuple[FeatureSpec, ...]:
        """Признаки со статусом active: именно они попадают в строку признаков."""
        return tuple(f for f in self.features if f.status is FeatureStatus.ACTIVE)

    def by_id(self) -> dict[str, FeatureSpec]:
        """Признаки по идентификатору."""
        return {f.id: f for f in self.features}


def load_feature_specs(root: Path | None = None) -> FeaturesContract:
    """Прочитать и проверить contracts/features/specs.yaml."""
    return load_model(contracts_root(root) / FEATURES_PATH, FeaturesContract)
