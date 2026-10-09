"""Тесты CLI-команды validate."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heliogeo.cli.main import app

runner = CliRunner()


def _valid_payload() -> dict:
    """Минимальный валидный payload."""
    now = datetime.now(UTC).isoformat()
    return {
        "metadata": {
            "issued_utc": now,
            "valid_until_utc": now,
            "generator_version": "1.0",
            "source_products": [],
        },
    }


def _extract_json(stdout: str) -> dict:
    """Извлечь JSON из stdout: от первой '{' до последней '}'."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON found in output: {stdout!r}")
    return json.loads(stdout[start : end + 1])


def test_validate_valid_file(tmp_path: Path) -> None:
    """Валидный JSON → exit 0, вывод OK."""
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0
    assert "OK" in result.stdout
    assert "issued_utc" in result.stdout


def test_validate_valid_file_json_output(tmp_path: Path) -> None:
    """--json → валидный JSON-ответ."""
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 0
    parsed = _extract_json(result.stdout)
    assert parsed["ok"] is True


def test_validate_invalid_schema(tmp_path: Path) -> None:
    """Битый JSON по схеме → exit 1, INVALID."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"metadata": {}}), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert "INVALID" in result.stdout


def test_validate_invalid_json_syntax(tmp_path: Path) -> None:
    """Сломанный JSON → exit 2, ERROR."""
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2
    assert "ERROR" in result.stdout


def test_validate_file_not_found() -> None:
    """Несуществующий файл → exit 2, ERROR."""
    result = runner.invoke(app, ["validate", "/tmp/does-not-exist.json"])
    assert result.exit_code == 2
    assert "ERROR" in result.stdout


def test_validate_not_an_object(tmp_path: Path) -> None:
    """JSON-массив (не объект) → exit 2, ERROR."""
    path = tmp_path / "array.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2
    assert "ERROR" in result.stdout


def test_validate_invalid_schema_json_output(tmp_path: Path) -> None:
    """--json на битом payload → валидный JSON с errors."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"metadata": {}}), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 1
    parsed = _extract_json(result.stdout)
    assert parsed["ok"] is False
    assert parsed["error_count"] >= 1
    assert any(
        "metadata" in e["loc"] or "issued_utc" in e["loc"] for e in parsed["errors"]
    )


@pytest.mark.parametrize("path_in_stdin", ["-"])
def test_validate_stdin(path_in_stdin: str) -> None:
    """validate - читает из stdin."""
    data = json.dumps(_valid_payload())
    result = runner.invoke(app, ["validate", path_in_stdin], input=data)
    assert result.exit_code == 0
    assert "OK" in result.stdout
