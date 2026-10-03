"""Тесты lock-файла контрактов: версии, хэши, обновление, сверка, CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from heliogeo.contracts import lock
from heliogeo.contracts.kinds import KINDS, load_all
from heliogeo.contracts.loader import ContractError
from heliogeo.contracts.lock import (
    LOCK_NAME,
    LockEntry,
    LockFile,
    State,
    check_lock,
    classify,
    plan_update,
    read_lock,
    update_lock,
    write_lock,
)
from heliogeo.contracts.validate import validate_directory
from tests.mocks.contracts import (
    SIGNATURE,
    contract_data,
    make_contracts_tree,
    make_feature,
    write_contract,
)

runner = CliRunner()
SHA_A = "a" * 64
SHA_B = "b" * 64
FACTS = "facts/registry.yaml"
LEXICON = "lexicon/terms.yaml"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Дерево контрактов без lock-файла."""
    return make_contracts_tree(tmp_path / "contracts")


@pytest.fixture
def locked(root: Path) -> Path:
    """Дерево контрактов с актуальным lock-файлом."""
    update_lock(root)
    return root


def _entry(version: str = "0.1.0", sha: str = SHA_A) -> LockEntry:
    return LockEntry(version=version, sha256=sha)


def _edit(root: Path, kind_name: str, path: str, **changes: Any) -> None:
    write_contract(root, path, contract_data(kind_name, **changes))


# --- classify ---


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, _entry(), State.ADDED),
        (_entry(), _entry(), State.UNCHANGED),
        (_entry(), _entry(sha=SHA_B), State.UNBUMPED),
        (_entry(), _entry("0.2.0"), State.BUMPED),
        (_entry(), _entry("0.2.0", SHA_B), State.BUMPED),
        (_entry("0.2.0"), _entry("0.1.0"), State.DOWNGRADED),
        (_entry("0.2.0"), _entry("0.1.0", SHA_B), State.DOWNGRADED),
        # версии сравниваются по числам: 0.10.0 новее 0.9.0 (по строкам было бы наоборот)
        (_entry("0.9.0"), _entry("0.10.0"), State.BUMPED),
        (_entry("2026.9.3"), _entry("2026.10.1"), State.BUMPED),
        # одна и та же версия, записанная по-разному
        (_entry("2026.09.1"), _entry("2026.9.1"), State.UNCHANGED),
        (_entry("2026.09.1"), _entry("2026.9.1", SHA_B), State.UNBUMPED),
    ],
)
def test_classify(old: LockEntry | None, new: LockEntry, expected: State) -> None:
    assert classify(old, new) is expected


# --- чтение и запись ---


def test_read_lock_without_file_returns_none(tmp_path: Path) -> None:
    assert read_lock(tmp_path) is None


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    original = LockFile(files={"b/z.yaml": _entry("1.0.0", SHA_B), "a/y.yaml": _entry()})
    write_lock(tmp_path, original)
    assert read_lock(tmp_path) == original


def test_lock_file_text_is_stable(tmp_path: Path) -> None:
    """Ключи отсортированы, отступ 2, окончания строк LF, перевод строки в конце."""
    write_lock(tmp_path, LockFile(files={"b.yaml": _entry("1.0.0", SHA_B), "a.yaml": _entry()}))
    raw = (tmp_path / LOCK_NAME).read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"}\n")
    data = json.loads(raw)
    assert list(data) == ["files", "format"]
    assert list(data["files"]) == ["a.yaml", "b.yaml"]
    assert raw.decode("utf-8").splitlines()[1].startswith('  "files"')


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{not json", "файл не читается"),
        ("[]", "ожидался словарь"),
        ('{"format": 2, "files": {}}', "format"),
        ('{"format": 1}', "files: обязательное поле отсутствует"),
        ('{"format": 1, "files": {}, "extra": 1}', "extra: неизвестное поле"),
        (
            '{"format": 1, "files": {"a.yaml": {"version": "1", "sha256": "' + SHA_A + '"}}}',
            "version",
        ),
        ('{"format": 1, "files": {"a.yaml": {"version": "1.0.0", "sha256": "xyz"}}}', "sha256"),
    ],
)
def test_read_lock_rejects_corrupt_files(tmp_path: Path, text: str, expected: str) -> None:
    (tmp_path / LOCK_NAME).write_text(text, encoding="utf-8")
    with pytest.raises(ContractError) as exc_info:
        read_lock(tmp_path)
    assert any(expected in problem for problem in exc_info.value.problems)


# --- update_lock ---


def test_first_run_creates_lock_with_all_contracts(root: Path) -> None:
    changes = update_lock(root)
    assert [c.state for c in changes] == [State.ADDED] * len(KINDS)
    assert [c.path for c in changes] == [kind.path for kind in KINDS]

    contracts, _ = load_all(root)
    stored = read_lock(root)
    assert stored is not None
    assert stored.files == {c.kind.path: _entry("0.1.0", c.sha256) for c in contracts}


def test_second_run_changes_nothing(locked: Path) -> None:
    before = (locked / LOCK_NAME).read_bytes()
    assert update_lock(locked) == []
    assert (locked / LOCK_NAME).read_bytes() == before


def test_content_change_without_version_bump_is_refused(locked: Path) -> None:
    before = (locked / LOCK_NAME).read_bytes()
    _edit(locked, "facts", FACTS, facts=[{"id": "new.fact"}])
    with pytest.raises(ContractError) as exc_info:
        update_lock(locked)
    assert exc_info.value.problems == [
        f"{FACTS}: содержимое изменено, а version не повышена (сейчас 0.1.0)"
    ]
    assert (locked / LOCK_NAME).read_bytes() == before  # lock не тронут


def test_content_change_with_version_bump_updates_lock(locked: Path) -> None:
    _edit(locked, "facts", FACTS, version="0.2.0", facts=[{"id": "new.fact"}])
    (change,) = update_lock(locked)
    assert (change.path, change.state) == (FACTS, State.BUMPED)
    assert change.old is not None and change.new is not None
    assert (change.old.version, change.new.version) == ("0.1.0", "0.2.0")
    assert change.old.sha256 != change.new.sha256

    stored = read_lock(locked)
    assert stored is not None
    assert stored.files[FACTS] == change.new
    assert update_lock(locked) == []


def test_version_bump_without_content_change_is_recorded(locked: Path) -> None:
    _edit(locked, "facts", FACTS, version="0.1.1")
    (change,) = update_lock(locked)
    assert change.state is State.BUMPED
    assert change.old is not None and change.new is not None
    assert change.old.sha256 == change.new.sha256


def test_version_downgrade_is_refused(locked: Path) -> None:
    _edit(locked, "facts", FACTS, version="0.2.0")
    update_lock(locked)
    _edit(locked, "facts", FACTS, version="0.1.5")
    with pytest.raises(ContractError, match=r"version понижена \(0.2.0 -> 0.1.5\)"):
        update_lock(locked)


def test_comments_quotes_and_signature_do_not_require_a_bump(locked: Path) -> None:
    """Хэш — по смыслу данных: комментарии, кавычки и подпись эксперта его не меняют."""
    (locked / FACTS).write_text(
        '# комментарий\nversion: "0.1.0"\napproved_by: "Иванов И.И."\n'
        'approved_at: "2026-09-15"\n',
        encoding="utf-8",
    )
    assert update_lock(locked) == []
    assert validate_directory(locked).ok


def test_all_problems_are_reported_together(locked: Path) -> None:
    _edit(locked, "facts", FACTS, facts=[{"id": "a"}])
    _edit(locked, "lexicon", LEXICON, terms=[{"id": "b"}])
    with pytest.raises(ContractError) as exc_info:
        update_lock(locked)
    assert [p.split(":")[0] for p in exc_info.value.problems] == [FACTS, LEXICON]


def test_invalid_contract_blocks_the_update(locked: Path) -> None:
    before = (locked / LOCK_NAME).read_bytes()
    _edit(locked, "facts", FACTS, version=1.0)
    with pytest.raises(ContractError) as exc_info:
        update_lock(locked)
    assert exc_info.value.problems[0].startswith(f"{FACTS}: version: версия должна быть строкой")
    assert (locked / LOCK_NAME).read_bytes() == before


def test_stale_entries_are_removed(locked: Path) -> None:
    stored = read_lock(locked)
    assert stored is not None
    write_lock(locked, LockFile(files={**stored.files, "old/gone.yaml": _entry("3.0.0", SHA_B)}))

    (change,) = update_lock(locked)
    assert (change.path, change.state, change.new) == ("old/gone.yaml", State.REMOVED, None)
    again = read_lock(locked)
    assert again is not None
    assert "old/gone.yaml" not in again.files


def test_plan_update_does_not_touch_files(root: Path) -> None:
    contracts, _ = load_all(root)
    new_lock, changes = plan_update(None, contracts)
    assert len(new_lock.files) == len(changes) == len(KINDS)
    assert not (root / LOCK_NAME).exists()


# --- check_lock ---


def test_check_lock_without_file(root: Path) -> None:
    contracts, _ = load_all(root)
    assert check_lock(None, contracts) == (
        {},
        [f"нет {LOCK_NAME}: выполните `make contracts-lock`"],
    )


def test_check_lock_in_sync(locked: Path) -> None:
    contracts, _ = load_all(locked)
    assert check_lock(read_lock(locked), contracts) == ({}, [])


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"facts": [{"id": "a"}]}, "содержимое изменено, а version не повышена (сейчас 0.1.0)"),
        ({"version": "0.2.0"}, "versions.lock.json устарел (version 0.1.0 -> 0.2.0)"),
        ({"version": "0.0.9"}, "version понижена (0.1.0 -> 0.0.9)"),
    ],
    ids=["unbumped", "stale", "downgraded"],
)
def test_check_lock_reports_mismatches_per_file(
    locked: Path, changes: dict[str, Any], expected: str
) -> None:
    _edit(locked, "facts", FACTS, **changes)
    contracts, _ = load_all(locked)
    by_path, general = check_lock(read_lock(locked), contracts)
    assert general == []
    assert list(by_path) == [FACTS]
    assert expected in by_path[FACTS][0]


def test_check_lock_reports_missing_entry_and_stale_entry(locked: Path) -> None:
    stored = read_lock(locked)
    assert stored is not None
    files = {path: entry for path, entry in stored.files.items() if path != FACTS}
    files["old/gone.yaml"] = _entry()
    contracts, _ = load_all(locked)

    by_path, general = check_lock(LockFile(files=files), contracts)
    assert by_path == {FACTS: [f"нет в {LOCK_NAME}: выполните `make contracts-lock`"]}
    assert general == [
        "old/gone.yaml: есть в versions.lock.json, но не зарегистрирован в "
        "heliogeo.contracts.kinds.KINDS (выполните `make contracts-lock`)"
    ]


# --- интеграция с валидатором ---


def test_validator_requires_lock_file(root: Path) -> None:
    report = validate_directory(root)
    assert report.problems == [f"нет {LOCK_NAME}: выполните `make contracts-lock`"]
    assert validate_directory(root, check_lock=False).ok


def test_validator_attaches_lock_errors_to_the_file(locked: Path) -> None:
    _edit(
        locked, "features", "features/specs.yaml", features=[make_feature(available_delay="PT1H")]
    )
    report = validate_directory(locked)
    (broken,) = (f for f in report.files if f.errors)
    assert broken.path == "features/specs.yaml"
    assert "содержимое изменено, а version не повышена" in broken.errors[0]

    _edit(
        locked,
        "features",
        "features/specs.yaml",
        version="0.1.1",
        features=[make_feature(available_delay="PT1H")],
    )
    assert "устарел" in validate_directory(locked).files[-1].errors[0]
    update_lock(locked)
    assert validate_directory(locked).ok


def test_validator_reports_corrupt_lock(locked: Path) -> None:
    (locked / LOCK_NAME).write_text("{oops", encoding="utf-8")
    report = validate_directory(locked)
    assert len(report.problems) == 1
    assert report.problems[0].startswith(f"{LOCK_NAME}: файл не читается")


def test_validator_skips_lock_for_contracts_that_failed_to_load(locked: Path) -> None:
    """Сломанный файл даёт одну ошибку, а не ещё и расхождение с lock-файлом."""
    _edit(locked, "facts", FACTS, version="v1")
    report = validate_directory(locked)
    (item,) = (f for f in report.files if f.path == FACTS)
    assert len(item.errors) == 1


def test_signing_a_locked_contract_keeps_validation_green(locked: Path) -> None:
    for kind in KINDS:
        write_contract(locked, kind.path, contract_data(kind.name, **SIGNATURE))
    assert validate_directory(locked, strict=True).ok


# --- CLI ---


def test_cli_creates_and_reports(root: Path) -> None:
    first = runner.invoke(lock.app, ["--dir", str(root)])
    assert first.exit_code == 0, first.output
    for kind in KINDS:
        assert f"добавлен: {kind.path} (0.1.0)" in first.output

    second = runner.invoke(lock.app, ["--dir", str(root)])
    assert second.exit_code == 0
    assert second.output.strip() == f"{LOCK_NAME}: без изменений"


def test_cli_refuses_changes_without_bump(locked: Path) -> None:
    _edit(locked, "facts", FACTS, facts=[{"id": "a"}])
    result = runner.invoke(lock.app, ["--dir", str(locked)])
    assert result.exit_code == 1
    assert f"ОШИБКА: {FACTS}: содержимое изменено, а version не повышена" in result.output


def test_cli_reports_bumped_and_removed(locked: Path) -> None:
    stored = read_lock(locked)
    assert stored is not None
    write_lock(locked, LockFile(files={**stored.files, "old/gone.yaml": _entry()}))
    _edit(locked, "facts", FACTS, version="0.2.0")
    result = runner.invoke(lock.app, ["--dir", str(locked)])
    assert result.exit_code == 0, result.output
    assert f"обновлён: {FACTS} (0.1.0 -> 0.2.0)" in result.output
    assert "удалён: old/gone.yaml" in result.output


def test_cli_fails_on_invalid_contracts(locked: Path) -> None:
    _edit(locked, "facts", FACTS, version="v1")
    result = runner.invoke(lock.app, ["--dir", str(locked)])
    assert result.exit_code == 1
    assert f"ОШИБКА: {FACTS}: version: версия должна иметь вид X.Y.Z" in result.output


def test_module_entry_point_creates_lock(root: Path) -> None:
    """python -m heliogeo.contracts.lock в настоящем процессе."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "heliogeo.contracts.lock", "--dir", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert (root / LOCK_NAME).is_file()
