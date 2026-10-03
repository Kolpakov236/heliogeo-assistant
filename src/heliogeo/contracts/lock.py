"""Lock-файл контрактов (versions.lock.json): версия и хэш тела каждого контракта.

Зачем: содержимое контракта нельзя изменить, не повысив ``version``. Валидатор сравнивает
файлы с lock-файлом, а обновляет его команда ``python -m heliogeo.contracts.lock`` — и
только если у каждого изменённого контракта версия выросла. Подпись эксперта (approved_by,
approved_at) в хэш не входит, поэтому подписать уже слитую версию можно без её повышения.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heliogeo.contracts.envelope import version_key
from heliogeo.contracts.kinds import KINDS, LoadedContract, load_all
from heliogeo.contracts.loader import ContractError, contracts_root, format_validation_error

LOCK_NAME = "versions.lock.json"
_HINT = "выполните `make contracts-lock`"


class LockEntry(BaseModel):
    """Запись lock-файла: версия контракта и хэш его тела."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LockFile(BaseModel):
    """Содержимое versions.lock.json: путь контракта -> запись."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = 1
    files: dict[str, LockEntry]


class State(StrEnum):
    """Как контракт соотносится со своей записью в lock-файле."""

    UNCHANGED = "unchanged"
    ADDED = "added"  # записи в lock-файле ещё нет
    BUMPED = "bumped"  # версия выросла: запись устарела
    UNBUMPED = "unbumped"  # содержимое изменено, а версия прежняя
    DOWNGRADED = "downgraded"  # версия понижена
    REMOVED = "removed"  # в lock-файле есть запись о контракте, которого больше нет


def classify(old: LockEntry | None, new: LockEntry) -> State:
    """Состояние контракта относительно записи в lock-файле (версии сравниваются по числам)."""
    if old is None:
        return State.ADDED
    if version_key(new.version) == version_key(old.version):
        return State.UNCHANGED if new.sha256 == old.sha256 else State.UNBUMPED
    if version_key(new.version) > version_key(old.version):
        return State.BUMPED
    return State.DOWNGRADED


def read_lock(root: Path) -> LockFile | None:
    """Прочитать lock-файл; None, если его нет."""
    path = root / LOCK_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(str(path), [f"файл не читается: {exc}"]) from exc
    try:
        return LockFile.model_validate(data)
    except ValidationError as exc:
        raise ContractError(str(path), format_validation_error(exc)) from exc


def write_lock(root: Path, lock: LockFile) -> None:
    """Записать lock-файл: ключи отсортированы, окончания строк LF."""
    text = json.dumps(lock.model_dump(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / LOCK_NAME).write_text(text, encoding="utf-8", newline="\n")


def _entry(contract: LoadedContract) -> LockEntry:
    return LockEntry(version=contract.model.version, sha256=contract.sha256)


def _mismatch(state: State, old: LockEntry | None, new: LockEntry) -> str:
    """Сообщение о расхождении контракта с lock-файлом (state не UNCHANGED)."""
    previous = old.version if old else "-"
    if state is State.ADDED:
        return f"нет в {LOCK_NAME}: {_HINT}"
    if state is State.UNBUMPED:
        return (
            f"содержимое изменено, а version не повышена (сейчас {new.version}): "
            f"поднимите version и {_HINT}"
        )
    if state is State.BUMPED:
        return f"{LOCK_NAME} устарел (version {previous} -> {new.version}): {_HINT}"
    return f"version понижена ({previous} -> {new.version})"


def check_lock(
    lock: LockFile | None, contracts: Sequence[LoadedContract]
) -> tuple[dict[str, list[str]], list[str]]:
    """Сверить контракты с lock-файлом: (сообщения по путям файлов, общие сообщения)."""
    if lock is None:
        return {}, [f"нет {LOCK_NAME}: {_HINT}"]

    by_path: dict[str, list[str]] = {}
    for contract in contracts:
        old, new = lock.files.get(contract.kind.path), _entry(contract)
        state = classify(old, new)
        if state is not State.UNCHANGED:
            by_path[contract.kind.path] = [_mismatch(state, old, new)]

    registered = {kind.path for kind in KINDS}
    general = [
        f"{path}: есть в {LOCK_NAME}, но не зарегистрирован в heliogeo.contracts.kinds.KINDS "
        f"({_HINT})"
        for path in sorted(set(lock.files) - registered)
    ]
    return by_path, general


@dataclass(frozen=True)
class LockChange:
    """Изменение записи lock-файла при обновлении."""

    path: str
    state: State
    old: LockEntry | None
    new: LockEntry | None


def plan_update(
    old: LockFile | None, contracts: Sequence[LoadedContract]
) -> tuple[LockFile, list[LockChange]]:
    """Новый lock-файл и список изменений.

    Raises:
        ContractError: контракт изменён без повышения version или версия понижена.
    """
    previous = old.files if old else {}
    registered = {kind.path for kind in KINDS}
    entries: dict[str, LockEntry] = {}
    changes: list[LockChange] = []
    problems: list[str] = []

    for contract in contracts:
        path = contract.kind.path
        new = _entry(contract)
        state = classify(previous.get(path), new)
        if state is State.UNBUMPED:
            problems.append(
                f"{path}: содержимое изменено, а version не повышена (сейчас {new.version})"
            )
        elif state is State.DOWNGRADED:
            problems.append(f"{path}: version понижена ({previous[path].version} -> {new.version})")
        elif state is State.UNCHANGED:
            entries[path] = previous[path]
        else:
            entries[path] = new
            changes.append(LockChange(path, state, previous.get(path), new))

    if problems:
        raise ContractError(LOCK_NAME, problems)

    changes.extend(
        LockChange(path, State.REMOVED, entry, None)
        for path, entry in sorted(previous.items())
        if path not in registered
    )
    return LockFile(files=entries), changes


def update_lock(root: Path) -> list[LockChange]:
    """Обновить lock-файл по текущим контрактам и вернуть список изменений.

    Raises:
        ContractError: контракты не проходят проверку или изменены без повышения version.
    """
    contracts, errors = load_all(root)
    if errors:
        raise ContractError(
            str(root),
            [f"{path}: {problem}" for path, problems in errors.items() for problem in problems],
        )
    lock, changes = plan_update(read_lock(root), contracts)
    if changes:
        write_lock(root, lock)
    return changes


app = typer.Typer(add_completion=False, help=f"Обновление {LOCK_NAME} по текущим контрактам.")


def _describe(change: LockChange) -> str:
    old, new = change.old, change.new
    if change.state is State.ADDED and new:
        return f"добавлен: {change.path} ({new.version})"
    if change.state is State.BUMPED and old and new:
        return f"обновлён: {change.path} ({old.version} -> {new.version})"
    return f"удалён: {change.path}"


@app.command()
def main(
    directory: Path | None = typer.Option(
        None, "--dir", help="Каталог контрактов (по умолчанию CONTRACTS_DIR или contracts/)."
    ),
) -> None:
    """Обновить lock-файл; код возврата 1, если контракты не проходят проверку."""
    try:
        changes = update_lock(contracts_root(directory))
    except ContractError as exc:
        for problem in exc.problems:
            typer.secho(f"ОШИБКА: {problem}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    if not changes:
        typer.echo(f"{LOCK_NAME}: без изменений")
    for change in changes:
        typer.echo(_describe(change))


if __name__ == "__main__":
    app()
