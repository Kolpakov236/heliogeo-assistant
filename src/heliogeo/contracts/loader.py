"""Чтение контрактов: безопасный YAML без дублей ключей, сообщения об ошибках, хэш тела."""

import hashlib
import json
from collections.abc import Hashable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError

from heliogeo.contracts.envelope import ENVELOPE_KEYS, ContractModel
from heliogeo.core.config import get_settings

ModelT = TypeVar("ModelT", bound=ContractModel)

_MERGE_TAG = "tag:yaml.org,2002:merge"

# Типы pydantic, у которых понятнее русское сообщение
_ERROR_TEXTS = {
    "missing": "обязательное поле отсутствует",
    "extra_forbidden": "неизвестное поле",
    "model_type": "ожидался словарь",
}
_VALUE_ERROR_PREFIX = "Value error, "


class ContractError(Exception):
    """Контракт нельзя прочитать или он не проходит проверку."""

    def __init__(self, path: str, problems: Sequence[str]) -> None:
        self.path = path
        self.problems = list(problems)
        super().__init__(f"{path}: " + "; ".join(self.problems))


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader, отвергающий повторяющиеся ключи (PyYAML молча берёт последний)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:  # «<<: *anchor» может встречаться несколько раз
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                continue  # нехешируемый ключ отвергнет сам SafeLoader
            if key in seen:
                raise ConstructorError(
                    None, None, f"повторяющийся ключ {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _yaml_problem(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        return f"{exc.problem} (строка {exc.problem_mark.line + 1})"
    return str(exc)


def read_mapping(path: Path) -> dict[str, Any]:
    """Прочитать YAML-файл, корнем которого должен быть словарь со строковыми ключами."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ContractError(str(path), ["файл не найден"]) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractError(str(path), [f"файл не читается: {exc}"]) from exc

    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ContractError(str(path), [f"некорректный YAML: {_yaml_problem(exc)}"]) from exc

    if not isinstance(data, dict):
        raise ContractError(
            str(path),
            ["корнем файла должен быть словарь: version, approved_by, approved_at и тело"],
        )
    if not all(isinstance(key, str) for key in data):
        raise ContractError(str(path), ["ключи верхнего уровня должны быть строками"])
    return data


def _format_location(location: tuple[int | str, ...]) -> str:
    """('features', 3, 'available_delay') -> 'features[3].available_delay'."""
    result = ""
    for part in location:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def format_validation_error(exc: ValidationError) -> list[str]:
    """Ошибки pydantic в виде строк «путь.к.полю: сообщение»."""
    lines = []
    for error in exc.errors():
        message = _ERROR_TEXTS.get(error["type"], error["msg"])
        message = message.removeprefix(_VALUE_ERROR_PREFIX)
        location = _format_location(error["loc"])
        lines.append(f"{location}: {message}" if location else message)
    return lines


def validate_model(data: Mapping[str, Any], model: type[ModelT], path: str) -> ModelT:
    """Проверить словарь моделью контракта; ошибки собираются в ContractError."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ContractError(path, format_validation_error(exc)) from exc


def load_model(path: Path, model: type[ModelT]) -> ModelT:
    """Прочитать файл и проверить его моделью контракта."""
    return validate_model(read_mapping(path), model, str(path))


def _canonical(value: Any, path: str) -> Any:
    """Привести значение YAML к JSON-совместимому виду (даты — строкой ISO)."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ContractError(path, ["ключи словарей должны быть строками"])
        return {key: _canonical(item, path) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item, path) for item in value]
    if isinstance(value, date):  # datetime — подкласс date
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ContractError(path, [f"неподдерживаемый тип значения: {type(value).__name__}"])


def body_sha256(data: Mapping[str, Any], path: str = "<контракт>") -> str:
    """SHA-256 тела контракта — без version, approved_by и approved_at.

    Хэш считается по разобранным данным с отсортированными ключами, поэтому комментарии,
    порядок ключей, кавычки и окончания строк (CRLF/LF) на него не влияют: он меняется
    только при смысловой правке. Подпись эксперта в хэш не входит: подписать версию можно
    без её повышения.
    """
    body = {key: value for key, value in data.items() if key not in ENVELOPE_KEYS}
    try:
        encoded = json.dumps(
            _canonical(body, path),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as exc:
        raise ContractError(path, ["значения NaN и Infinity не допускаются"]) from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def contracts_root(root: Path | None = None) -> Path:
    """Каталог контрактов: явный аргумент или Settings.contracts_dir."""
    return Path(root) if root is not None else get_settings().contracts_dir
