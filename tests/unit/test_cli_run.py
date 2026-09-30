"""Тесты CLI-команды run."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from heliogeo.cli.main import app
from heliogeo.schema import Metadata, UnifiedPayload

runner = CliRunner()


@pytest.fixture(autouse=True)
def mock_build_payload():
    """Мокаем build_payload, чтобы тесты не ходили в реальный API."""
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    with patch(
        "heliogeo.pipeline.pipeline.build_payload",
        return_value=payload,
    ):
        yield


def test_run_help() -> None:
    """heliogeo run --help показывает опции."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--product" in result.stdout
    assert "--provider" in result.stdout
    assert "--deliver" in result.stdout


def test_run_mock_single_product(tmp_path: Path) -> None:
    """run с mock-провайдером и одним продуктом."""
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "icao_brief",
            "--provider",
            "mock",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Pipeline: OK" in result.stdout
    assert "icao_brief.txt" in result.stdout


def test_run_mock_all_products(tmp_path: Path) -> None:
    """run с --product all генерирует оба продукта."""
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "all",
            "--provider",
            "mock",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "icao_brief.txt" in result.stdout
    assert "swpc_discussion.txt" in result.stdout


def test_run_with_pdf_format(tmp_path: Path) -> None:
    """run с --format pdf создаёт PDF."""
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "icao_brief",
            "--format",
            "txt",
            "--format",
            "pdf",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "icao_brief.txt" in result.stdout
    assert "icao_brief.pdf" in result.stdout
    assert (tmp_path / "icao_brief.pdf").exists()


def test_run_with_mock_delivery(tmp_path: Path) -> None:
    """run с --deliver mock не падает."""
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "icao_brief",
            "--deliver",
            "mock",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Pipeline: OK" in result.stdout


def test_run_unknown_provider(tmp_path: Path) -> None:
    """run с неизвестным провайдером → ошибка."""
    result = runner.invoke(
        app,
        [
            "run",
            "--provider",
            "unknown",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0


def test_run_creates_output_dir(tmp_path: Path) -> None:
    """run создаёт папку output, если её нет."""
    output_dir = tmp_path / "nested" / "outputs"
    result = runner.invoke(
        app,
        [
            "run",
            "--output",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0
    assert output_dir.exists()
