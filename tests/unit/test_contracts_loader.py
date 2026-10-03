"""Тесты загрузчика контрактов: YAML, сообщения об ошибках, хэш тела, каталог."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from heliogeo.contracts import loader
from heliogeo.contracts.envelope import ContractModel
from heliogeo.contracts.loader import (
    ContractError,
    body_sha256,
    contracts_root,
    load_model,
    read_mapping,
    validate_model,
)
from heliogeo.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
ENVELOPE_YAML = 'version: "0.1.0"\napproved_by: null\napproved_at: null\n'


class _Item(BaseModel):
    name: str


class _Doc(ContractModel):
    model_config = ConfigDict(extra="forbid")

    items: list[_Item]


def _write(tmp_path: Path, text: str, name: str = "c.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _problems(exc_info: pytest.ExceptionInfo[ContractError]) -> list[str]:
    return exc_info.value.problems


# --- read_mapping ---


def test_read_mapping_returns_dict(tmp_path: Path) -> None:
    path = _write(tmp_path, ENVELOPE_YAML + "items:\n  - name: a\n")
    assert read_mapping(path) == {
        "version": "0.1.0",
        "approved_by": None,
        "approved_at": None,
        "items": [{"name": "a"}],
    }


def test_read_mapping_parses_crlf_files(tmp_path: Path) -> None:
    """Рабочее дерево на Windows (autocrlf) даёт CRLF — читается так же."""
    path = tmp_path / "c.yaml"
    path.write_bytes((ENVELOPE_YAML + "items: []\n").replace("\n", "\r\n").encode("utf-8"))
    assert read_mapping(path)["items"] == []


def test_duplicate_key_is_reported_with_line(tmp_path: Path) -> None:
    """PyYAML молча берёт последний дубль; здесь это ошибка с номером строки."""
    path = _write(tmp_path, 'version: "0.1.0"\na: 1\na: 2\n')
    with pytest.raises(ContractError) as exc_info:
        read_mapping(path)
    (problem,) = _problems(exc_info)
    assert "повторяющийся ключ 'a'" in problem
    assert "строка 3" in problem


def test_duplicate_key_in_nested_mapping_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "items:\n  - name: a\n    name: b\n")
    with pytest.raises(ContractError, match="повторяющийся ключ 'name'"):
        read_mapping(path)


def test_merge_keys_are_not_duplicates(tmp_path: Path) -> None:
    """Якоря и «<<» допустимы: ключ слияния может встречаться в одном словаре несколько раз."""
    text = "base: &b {x: 1}\nother: &o {z: 3}\nitem:\n  <<: *b\n  <<: *o\n  y: 2\n"
    assert read_mapping(_write(tmp_path, text))["item"] == {"x": 1, "z": 3, "y": 2}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- a\n- b\n", "корнем файла должен быть словарь"),
        ("just text\n", "корнем файла должен быть словарь"),
        ("", "корнем файла должен быть словарь"),
        ("a: [1, 2\n", "некорректный YAML"),
        ("1: x\n", "ключи верхнего уровня должны быть строками"),
    ],
    ids=["list", "scalar", "empty", "broken", "non-string-key"],
)
def test_read_mapping_rejects_unusable_files(tmp_path: Path, text: str, expected: str) -> None:
    with pytest.raises(ContractError, match=expected):
        read_mapping(_write(tmp_path, text))


def test_read_mapping_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="файл не найден"):
        read_mapping(tmp_path / "nope.yaml")


def test_read_mapping_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_bytes(b"a: \xff\xfe\n")
    with pytest.raises(ContractError, match="файл не читается"):
        read_mapping(path)


def test_unhashable_key_is_left_to_yaml_to_reject(tmp_path: Path) -> None:
    """Составной ключ (список) отвергает сам SafeLoader; проверка дублей ему не мешает."""
    with pytest.raises(ContractError, match=r"некорректный YAML: .*unhashable.*строка \d+"):
        read_mapping(_write(tmp_path, "? [a, b]\n: 1\n"))


def test_control_character_in_file_is_reported(tmp_path: Path) -> None:
    """Ошибки YAML без позиции (например, управляющий символ) тоже становятся ContractError."""
    with pytest.raises(ContractError, match="некорректный YAML: .*unacceptable character"):
        read_mapping(_write(tmp_path, "a: \x07\n"))


def test_contract_error_carries_path_and_problems() -> None:
    error = ContractError("x.yaml", ["первая", "вторая"])
    assert error.path == "x.yaml"
    assert error.problems == ["первая", "вторая"]
    assert str(error) == "x.yaml: первая; вторая"


# --- сообщения об ошибках модели ---


def test_validate_model_formats_nested_locations() -> None:
    data = {"version": "0.1.0", "approved_by": None, "approved_at": None}
    with pytest.raises(ContractError) as exc_info:
        validate_model({**data, "items": [{"name": "a"}, {}]}, _Doc, "doc.yaml")
    assert _problems(exc_info) == ["items[1].name: обязательное поле отсутствует"]


def test_validate_model_translates_missing_extra_and_value_errors() -> None:
    data = {"version": "1.0.0.0", "items": [], "oops": 1}
    with pytest.raises(ContractError) as exc_info:
        validate_model(data, _Doc, "doc.yaml")
    problems = _problems(exc_info)
    assert "approved_by: обязательное поле отсутствует" in problems
    assert "approved_at: обязательное поле отсутствует" in problems
    assert "oops: неизвестное поле" in problems
    # префикс «Value error, » pydantic в сообщении не нужен
    assert any(p.startswith("version: версия должна иметь вид X.Y.Z") for p in problems)


def test_validate_model_says_when_an_item_is_not_a_mapping() -> None:
    data = {
        "version": "0.1.0",
        "approved_by": None,
        "approved_at": None,
        "items": [1, {"name": "a"}],
    }
    with pytest.raises(ContractError) as exc_info:
        validate_model(data, _Doc, "doc.yaml")
    assert _problems(exc_info) == ["items[0]: ожидался словарь"]


def test_model_level_error_has_no_location_prefix() -> None:
    data = {"version": "0.1.0", "approved_by": "Иванов И.И.", "approved_at": None, "items": []}
    with pytest.raises(ContractError) as exc_info:
        validate_model(data, _Doc, "doc.yaml")
    assert _problems(exc_info) == [
        "approved_by и approved_at заполняются вместе (для черновика оба null)"
    ]


def test_load_model_reads_and_validates(tmp_path: Path) -> None:
    path = _write(tmp_path, ENVELOPE_YAML + "items:\n  - name: a\n")
    doc = load_model(path, _Doc)
    assert [item.name for item in doc.items] == ["a"]

    broken = _write(tmp_path, ENVELOPE_YAML + "items:\n  - {}\n", name="broken.yaml")
    with pytest.raises(ContractError, match=r"items\[0\]\.name"):
        load_model(broken, _Doc)


# --- body_sha256 ---

_BODY: dict[str, Any] = {"b": [1, "два"], "a": {"y": True, "x": None}}


def test_body_sha256_golden_value() -> None:
    """Эталон: sha256 от канонического JSON {"a":{"x":null,"y":true},"b":[1,"два"]}.

    Если тест упал, изменилась канонизация — а с ней хэши во всех versions.lock.json.
    """
    data = {"version": "0.1.0", "approved_by": None, "approved_at": None, **_BODY}
    assert body_sha256(data) == "e133ac95f91e87fdcb26a6fb12c5d6d5a011854eace5a6800474bd39c5210de4"


def test_body_sha256_ignores_envelope_and_key_order() -> None:
    first = {"version": "0.1.0", "approved_by": None, "approved_at": None, **_BODY}
    second = {
        "b": [1, "два"],
        "approved_at": date(2026, 9, 15),
        "a": {"x": None, "y": True},
        "version": "9.9.9",
        "approved_by": "Иванов И.И.",
    }
    assert body_sha256(first) == body_sha256(second)


def test_body_sha256_changes_with_content() -> None:
    base = body_sha256(_BODY)
    assert body_sha256({**_BODY, "b": [1, "три"]}) != base
    assert body_sha256({**_BODY, "b": ["два", 1]}) != base  # порядок списка — тоже содержимое
    assert body_sha256({**_BODY, "c": 1}) != base


def test_body_sha256_treats_date_and_iso_string_alike() -> None:
    """2026-09-01 и "2026-09-01" в YAML — одно и то же значение для хэша."""
    assert body_sha256({"d": date(2026, 9, 1)}) == body_sha256({"d": "2026-09-01"})


def test_body_sha256_ignores_comments_quotes_and_line_endings(tmp_path: Path) -> None:
    plain = _write(tmp_path, ENVELOPE_YAML + "name: abc\nitems:\n  - 1\n", name="a.yaml")
    decorated = tmp_path / "b.yaml"
    decorated.write_bytes(
        (
            '# комментарий\nversion: "0.1.0"\napproved_by: null\napproved_at: null\n'
            'items:   # ещё комментарий\n  - 1\nname: "abc"\n'
        )
        .replace("\n", "\r\n")
        .encode("utf-8")
    )
    assert body_sha256(read_mapping(plain)) == body_sha256(read_mapping(decorated))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"x": b"bytes"}, "неподдерживаемый тип значения: bytes"),
        ({"x": {1: "int key"}}, "ключи словарей должны быть строками"),
        ({"x": float("nan")}, "NaN и Infinity"),
        ({"x": [float("inf")]}, "NaN и Infinity"),
    ],
    ids=["bytes", "non-string-key", "nan", "inf"],
)
def test_body_sha256_rejects_unhashable_content(body: dict[str, Any], expected: str) -> None:
    with pytest.raises(ContractError, match=expected):
        body_sha256(body)


# --- каталог контрактов ---


def test_contracts_root_prefers_explicit_argument(tmp_path: Path) -> None:
    assert contracts_root(tmp_path) == tmp_path
    assert contracts_root(str(tmp_path)) == tmp_path  # type: ignore[arg-type]


def test_contracts_root_defaults_to_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader, "get_settings", lambda: SimpleNamespace(contracts_dir=tmp_path))
    assert contracts_root() == tmp_path


def test_settings_contracts_dir_defaults_to_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTRACTS_DIR", raising=False)
    assert Settings(_env_file=None).contracts_dir == ROOT / "contracts"


def test_settings_relative_contracts_dir_is_counted_from_repository_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Относительный CONTRACTS_DIR не зависит от каталога, из которого запущен процесс."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTRACTS_DIR", "my_contracts")
    assert Settings(_env_file=None).contracts_dir == ROOT / "my_contracts"


def test_settings_contracts_dir_is_read_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    assert Settings(_env_file=None).contracts_dir == tmp_path
