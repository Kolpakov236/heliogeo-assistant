"""Тесты валидатора каталога контрактов: отчёт, правила, CLI."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from heliogeo.contracts import kinds, loader, validate
from heliogeo.contracts.kinds import KINDS, LoadedContract
from heliogeo.contracts.validate import find_duplicate_ids, validate_directory
from tests.mocks.contracts import (
    SIGNATURE,
    contract_data,
    make_contracts_tree,
    make_feature,
    write_contract,
)

runner = CliRunner()
ALL_PATHS = [kind.path for kind in KINDS]


def _validate(root: Path, **kwargs: Any) -> validate.Report:
    """Проверка правил без сверки с lock-файлом: lock покрыт в test_contracts_lock.py."""
    return validate_directory(root, check_lock=False, **kwargs)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return make_contracts_tree(tmp_path / "contracts")


@pytest.fixture
def locked_tree(tmp_path: Path) -> Path:
    """Дерево с versions.lock.json: CLI всегда сверяет контракты с lock-файлом."""
    return make_contracts_tree(tmp_path / "locked", lock=True)


def _file(report: validate.Report, path: str) -> validate.FileReport:
    (item,) = (f for f in report.files if f.path == path)
    return item


# --- validate_directory ---


def test_valid_tree_passes_with_unsigned_warnings(tree: Path) -> None:
    report = _validate(tree)
    assert report.ok
    assert [f.path for f in report.files] == ALL_PATHS
    assert all(f.version == "0.1.0" and not f.signed for f in report.files)
    assert report.error_count == 0
    assert report.warning_count == len(KINDS)  # по одному «не подписан» на файл
    assert report.problems == []


def test_signed_tree_has_no_warnings(tmp_path: Path) -> None:
    report = _validate(make_contracts_tree(tmp_path, **SIGNATURE))
    assert report.ok
    assert report.warning_count == 0
    assert all(f.signed for f in report.files)


def test_strict_turns_missing_signature_into_error(tree: Path, tmp_path: Path) -> None:
    strict = _validate(tree, strict=True)
    assert not strict.ok
    assert strict.error_count == len(KINDS)
    assert all("не подписан" in f.errors[0] for f in strict.files)

    signed = make_contracts_tree(tmp_path / "signed", **SIGNATURE)
    assert _validate(signed, strict=True).ok


def test_missing_registered_file_is_an_error(tree: Path) -> None:
    (tree / "lexicon" / "terms.yaml").unlink()
    report = _validate(tree)
    assert not report.ok
    assert _file(report, "lexicon/terms.yaml").errors == ["файл не найден"]
    assert _file(report, "facts/registry.yaml").ok  # остальные файлы проверены


@pytest.mark.parametrize("relative", ["rules/extra.yaml", "notes.yml", "deep/er/UPPER.YAML"])
def test_unregistered_yaml_file_is_reported(tree: Path, relative: str) -> None:
    write_contract(tree, relative, contract_data("facts"))
    report = _validate(tree)
    assert report.problems == [
        f"{relative}: файл не зарегистрирован в heliogeo.contracts.kinds.KINDS"
    ]
    assert not report.ok


def test_non_yaml_files_are_ignored(tree: Path) -> None:
    (tree / "README.md").write_text("текст", encoding="utf-8")
    (tree / "versions.lock.json").write_text("{}", encoding="utf-8")
    assert _validate(tree).problems == []


def test_envelope_error_is_reported_per_file(tree: Path) -> None:
    write_contract(tree, "facts/registry.yaml", contract_data("facts", version=1.0))
    item = _file(_validate(tree), "facts/registry.yaml")
    assert item.version is None
    assert len(item.errors) == 1
    assert item.errors[0].startswith("version: версия должна быть строкой в кавычках")


def test_body_error_of_strict_kind_is_reported_with_location(tree: Path) -> None:
    broken = contract_data(
        "features", features=[make_feature(), make_feature(id="b", available_delay="3h")]
    )
    write_contract(tree, "features/specs.yaml", broken)
    item = _file(_validate(tree), "features/specs.yaml")
    assert [e.split(":")[0] for e in item.errors] == ["features[1].available_delay"]


def test_duplicate_yaml_key_is_reported(tree: Path) -> None:
    path = tree / "rules" / "thresholds.yaml"
    path.write_text(
        'version: "0.1.0"\napproved_by: null\napproved_at: null\nx: 1\nx: 2\n', encoding="utf-8"
    )
    item = _file(_validate(tree), "rules/thresholds.yaml")
    assert any("повторяющийся ключ 'x'" in e and "строка 5" in e for e in item.errors)


def test_duplicate_ids_in_draft_contract_are_errors(tree: Path) -> None:
    data = contract_data("facts", facts=[{"id": "a"}, {"id": "b"}, {"id": "a"}])
    write_contract(tree, "facts/registry.yaml", data)
    item = _file(_validate(tree), "facts/registry.yaml")
    assert item.errors == ["facts[2]: повторяющийся id 'a' (уже есть в facts[0])"]


def test_duplicate_ids_across_groups_are_errors(tree: Path) -> None:
    data = contract_data(
        "lexicon",
        modality=[{"id": "mod.x"}],
        solar_state=[{"id": "sun.quiet"}, {"id": "mod.x"}],
    )
    write_contract(tree, "lexicon/terms.yaml", data)
    item = _file(_validate(tree), "lexicon/terms.yaml")
    assert item.errors == ["solar_state[1]: повторяющийся id 'mod.x' (уже есть в modality[0])"]


def test_directory_that_does_not_exist(tmp_path: Path) -> None:
    report = _validate(tmp_path / "nope")
    assert report.problems == [f"каталог контрактов не найден: {tmp_path / 'nope'}"]
    assert report.files == []
    assert not report.ok


def test_report_counts_errors_and_problems_together(tree: Path) -> None:
    (tree / "lexicon" / "terms.yaml").unlink()
    write_contract(tree, "extra.yaml", contract_data("facts"))
    report = _validate(tree)
    assert report.error_count == 2  # файл не найден + незарегистрированный файл


# --- проверки ссылок между контрактами ---


def test_cross_checks_receive_contracts_by_kind_name(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[set[str]] = []

    def check(contracts: Any) -> list[str]:
        seen.append(set(contracts))
        assert isinstance(contracts["facts"], LoadedContract)
        return ["факт ссылается на несуществующий термин"]

    monkeypatch.setattr(kinds, "CROSS_CHECKS", (check,))
    report = _validate(tree)
    assert seen == [{kind.name for kind in KINDS}]
    assert report.problems == ["факт ссылается на несуществующий термин"]
    assert not report.ok


def test_cross_checks_are_skipped_when_a_contract_failed_to_load(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(kinds, "CROSS_CHECKS", (lambda contracts: calls.append(1) or [],))
    (tree / "facts" / "registry.yaml").unlink()
    _validate(tree)
    assert calls == []


# --- find_duplicate_ids ---


def test_find_duplicate_ids_ignores_envelope_and_foreign_shapes() -> None:
    data = {
        "version": "0.1.0",
        "approved_by": None,
        "approved_at": None,
        "thresholds": {"HF": {"id": "x"}, "SEV": {"id": "x"}},  # словарь словарей — не списки
        "items": [{"id": 1}, {"id": 1}, {"name": "без id"}, "строка", None, {"id": "ok"}],
        "scalar": "id",
    }
    assert find_duplicate_ids(data) == []


def test_find_duplicate_ids_reports_each_repeat_against_first_occurrence() -> None:
    data = {"a": [{"id": "x"}, {"id": "y"}], "b": {"g": [{"id": "x"}, {"id": "y"}, {"id": "x"}]}}
    assert find_duplicate_ids(data) == [
        "b.g[0]: повторяющийся id 'x' (уже есть в a[0])",
        "b.g[1]: повторяющийся id 'y' (уже есть в a[1])",
        "b.g[2]: повторяющийся id 'x' (уже есть в a[0])",
    ]


# --- CLI ---


def test_cli_ok(locked_tree: Path) -> None:
    result = runner.invoke(validate.app, ["--dir", str(locked_tree)])
    assert result.exit_code == 0, result.output
    assert f"Файлов: {len(KINDS)}, ошибок: 0, предупреждений: {len(KINDS)}" in result.output
    for path in ALL_PATHS:
        assert path in result.output
    assert "предупреждение: не подписан экспертом" in result.output
    assert "FAIL" not in result.output


def test_cli_reports_errors_with_exit_code_1(locked_tree: Path) -> None:
    write_contract(locked_tree, "facts/registry.yaml", contract_data("facts", version="v1"))
    write_contract(locked_tree, "stray.yaml", contract_data("facts"))
    result = runner.invoke(validate.app, ["--dir", str(locked_tree)])
    assert result.exit_code == 1
    assert "FAIL  facts/registry.yaml" in result.output
    assert "ошибка: version: версия должна иметь вид X.Y.Z" in result.output
    assert "ОШИБКА: stray.yaml: файл не зарегистрирован" in result.output
    assert "ошибок: 2" in result.output


def test_cli_strict_requires_signatures(locked_tree: Path, tmp_path: Path) -> None:
    assert runner.invoke(validate.app, ["--dir", str(locked_tree), "--strict"]).exit_code == 1
    signed = make_contracts_tree(tmp_path / "signed", lock=True, **SIGNATURE)
    assert runner.invoke(validate.app, ["--dir", str(signed), "--strict"]).exit_code == 0


def test_cli_uses_contracts_dir_from_settings_by_default(
    locked_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(loader, "get_settings", lambda: SimpleNamespace(contracts_dir=locked_tree))
    result = runner.invoke(validate.app, [])
    assert result.exit_code == 0, result.output


def test_module_entry_point_runs_and_prints_cyrillic(locked_tree: Path) -> None:
    """python -m heliogeo.contracts.validate: код возврата и вывод в настоящем процессе."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "heliogeo.contracts.validate", "--dir", str(locked_tree), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    ok = run()
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "предупреждений" in ok.stdout
    assert "Traceback" not in ok.stderr
    assert "RuntimeWarning" not in ok.stderr  # пакет не импортирует validate до запуска модуля

    assert run("--strict").returncode == 1
