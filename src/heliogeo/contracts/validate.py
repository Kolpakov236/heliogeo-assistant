"""Проверка каталога контрактов: ``python -m heliogeo.contracts.validate``.

Что проверяется: каждый YAML-файл зарегистрирован в kinds.KINDS, а каждый зарегистрированный
файл существует; конверт и тело проходят модель своего вида; id внутри файла уникальны;
содержимое согласовано с versions.lock.json (менять его без повышения version нельзя);
пройдены проверки ссылок между контрактами. Код возврата: 0 — ошибок нет, 1 — есть ошибки.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from heliogeo.contracts import kinds, lock
from heliogeo.contracts.envelope import ENVELOPE_KEYS
from heliogeo.contracts.kinds import KINDS, LoadedContract, load_all
from heliogeo.contracts.loader import ContractError, contracts_root

_UNSIGNED = "не подписан экспертом (approved_by и approved_at равны null)"
_YAML_SUFFIXES = {".yaml", ".yml"}


@dataclass
class FileReport:
    """Результат проверки одного контракта."""

    path: str
    version: str | None = None
    signed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Report:
    """Результат проверки каталога: по файлам и проблемы каталога в целом."""

    files: list[FileReport] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.problems) + sum(len(item.errors) for item in self.files)

    @property
    def warning_count(self) -> int:
        return sum(len(item.warnings) for item in self.files)

    @property
    def ok(self) -> bool:
        return self.error_count == 0


def _id_lists(key: str, value: Any) -> Iterator[tuple[str, list[Any]]]:
    """Списки верхнего уровня и списки внутри групп (словарь списков, как в словаре терминов)."""
    if isinstance(value, list):
        yield key, value
    elif isinstance(value, Mapping):
        for group, items in value.items():
            if isinstance(items, list):
                yield f"{key}.{group}", items


def find_duplicate_ids(data: Mapping[str, Any]) -> list[str]:
    """Повторяющиеся id у элементов списков верхнего уровня (и списков в группах)."""
    first_seen: dict[str, str] = {}
    problems: list[str] = []
    for key, value in data.items():
        if key in ENVELOPE_KEYS:
            continue
        for where, items in _id_lists(key, value):
            for index, item in enumerate(items):
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                    continue
                location = f"{where}[{index}]"
                if item["id"] in first_seen:
                    problems.append(
                        f"{location}: повторяющийся id {item['id']!r} "
                        f"(уже есть в {first_seen[item['id']]})"
                    )
                else:
                    first_seen[item["id"]] = location
    return problems


def _unregistered_files(root: Path) -> list[str]:
    registered = {kind.path for kind in KINDS}
    found = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _YAML_SUFFIXES
    }
    return sorted(found - registered)


def _check_contract(contract: LoadedContract, *, strict: bool) -> FileReport:
    report = FileReport(
        contract.kind.path, version=contract.model.version, signed=contract.model.is_signed
    )
    report.errors.extend(find_duplicate_ids(contract.data))
    if not report.signed:
        (report.errors if strict else report.warnings).append(_UNSIGNED)
    return report


def _apply_lock(root: Path, loaded: Sequence[LoadedContract], report: Report) -> None:
    """Сверить контракты с lock-файлом и добавить расхождения в отчёт."""
    try:
        current = lock.read_lock(root)
    except ContractError as exc:
        report.problems.extend(f"{lock.LOCK_NAME}: {problem}" for problem in exc.problems)
        return
    by_path, general = lock.check_lock(current, loaded)
    for item in report.files:
        item.errors.extend(by_path.get(item.path, []))
    report.problems.extend(general)


def validate_directory(root: Path, *, strict: bool = False, check_lock: bool = True) -> Report:
    """Проверить каталог контрактов.

    Args:
        root: каталог контрактов.
        strict: требовать подпись эксперта у каждого контракта (для релиза).
        check_lock: сверять контракты с versions.lock.json (содержимое нельзя менять без
            повышения version).
    """
    report = Report()
    if not root.is_dir():
        report.problems.append(f"каталог контрактов не найден: {root}")
        return report

    report.problems.extend(
        f"{path}: файл не зарегистрирован в heliogeo.contracts.kinds.KINDS"
        for path in _unregistered_files(root)
    )

    loaded, errors = load_all(root)
    by_path = {contract.kind.path: contract for contract in loaded}
    for kind in KINDS:
        if kind.path in errors:
            report.files.append(FileReport(kind.path, errors=list(errors[kind.path])))
        else:
            report.files.append(_check_contract(by_path[kind.path], strict=strict))

    if check_lock:
        _apply_lock(root, loaded, report)
    if not errors:  # проверки ссылок между контрактами имеют смысл, только когда загружены все
        by_name: Mapping[str, LoadedContract] = {c.kind.name: c for c in loaded}
        for check in kinds.CROSS_CHECKS:
            report.problems.extend(check(by_name))
    return report


app = typer.Typer(add_completion=False, help="Проверка контрактов (каталог contracts/).")


def _print_report(report: Report) -> None:
    for item in report.files:
        status = typer.style("OK  ", fg=typer.colors.GREEN)
        if not item.ok:
            status = typer.style("FAIL", fg=typer.colors.RED)
        typer.echo(f"{status}  {item.path:<40} {item.version or '-'}")
        for message in item.errors:
            typer.secho(f"      ошибка: {message}", fg=typer.colors.RED)
        for message in item.warnings:
            typer.secho(f"      предупреждение: {message}", fg=typer.colors.YELLOW)
    for message in report.problems:
        typer.secho(f"ОШИБКА: {message}", fg=typer.colors.RED)
    typer.echo(
        f"Файлов: {len(report.files)}, ошибок: {report.error_count}, "
        f"предупреждений: {report.warning_count}"
    )


@app.command()
def main(
    directory: Path | None = typer.Option(
        None, "--dir", help="Каталог контрактов (по умолчанию CONTRACTS_DIR или contracts/)."
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Требовать подпись эксперта у каждого контракта (для релиза)."
    ),
) -> None:
    """Проверить контракты; код возврата 1, если найдены ошибки."""
    report = validate_directory(contracts_root(directory), strict=strict)
    _print_report(report)
    raise typer.Exit(code=0 if report.ok else 1)


if __name__ == "__main__":
    app()
