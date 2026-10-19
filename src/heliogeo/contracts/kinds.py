"""Реестр видов контрактов: какой файл какой моделью проверяется."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ConfigDict

from heliogeo.contracts.envelope import ContractModel
from heliogeo.contracts.features import FEATURES_PATH, FeaturesContract
from heliogeo.contracts.loader import ContractError, body_sha256, read_mapping, validate_model
from heliogeo.contracts.signals import SIGNALS_PATH, SignalsContract, check_signals


class DraftContract(ContractModel):
    """Вид, тело которого ещё не специфицировано: проверяется только конверт.

    Строгую модель тела вводит задача, которая наполняет файл (реестр фактов, словарь,
    каталог разрывов): достаточно заменить модель в KINDS.
    """

    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True)
class ContractKind:
    """Вид контракта: имя, путь файла внутри каталога контрактов и модель для проверки."""

    name: str
    path: str  # относительно каталога контрактов, разделитель «/»
    model: type[ContractModel]


# Каждый YAML-файл каталога contracts/ должен быть здесь зарегистрирован
KINDS: tuple[ContractKind, ...] = (
    ContractKind("facts", "facts/registry.yaml", DraftContract),
    ContractKind("lexicon", "lexicon/terms.yaml", DraftContract),
    ContractKind("thresholds", "rules/thresholds.yaml", DraftContract),
    ContractKind("homogenization", "homogenization/discontinuities.yaml", DraftContract),
    ContractKind("features", FEATURES_PATH, FeaturesContract),
    ContractKind("signals", SIGNALS_PATH, SignalsContract),
)


@dataclass(frozen=True)
class LoadedContract:
    """Прочитанный и проверенный контракт: исходные данные и модель."""

    kind: ContractKind
    data: dict[str, Any]
    model: ContractModel

    @property
    def sha256(self) -> str:
        """Хэш тела контракта (см. loader.body_sha256)."""
        return body_sha256(self.data, self.kind.path)


# Проверки ссылок между контрактами (например, факт ссылается на термин словаря). Получают
# загруженные контракты по имени вида и возвращают список проблем. Появятся вместе с реестром
# фактов и словарём: функцию достаточно добавить в этот кортеж.
CrossCheck = Callable[[Mapping[str, LoadedContract]], list[str]]


def _signals_match_features(loaded: Mapping[str, LoadedContract]) -> list[str]:
    """Правила сигналов ссылаются на существующие признаки с подходящими порогами."""
    signals, features = loaded["signals"].model, loaded["features"].model
    if not isinstance(signals, SignalsContract) or not isinstance(features, FeaturesContract):
        return []
    return check_signals(signals, features)


CROSS_CHECKS: tuple[CrossCheck, ...] = (_signals_match_features,)


def load_kind(root: Path, kind: ContractKind) -> LoadedContract:
    """Прочитать файл вида и проверить его моделью; ошибки — в ContractError."""
    path = root / kind.path
    data = read_mapping(path)
    return LoadedContract(kind, data, validate_model(data, kind.model, str(path)))


def load_all(root: Path) -> tuple[list[LoadedContract], dict[str, list[str]]]:
    """Загрузить все зарегистрированные контракты: (загруженные, ошибки по путям файлов)."""
    loaded: list[LoadedContract] = []
    errors: dict[str, list[str]] = {}
    for kind in KINDS:
        try:
            loaded.append(load_kind(root, kind))
        except ContractError as exc:
            errors[kind.path] = exc.problems
    return loaded, errors
