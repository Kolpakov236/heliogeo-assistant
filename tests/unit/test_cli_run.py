"""Тесты CLI-команды run."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import Result
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
    ) as builder:
        yield builder


def test_run_help() -> None:
    """heliogeo run --help показывает опции."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--product" in result.stdout
    assert "--provider" in result.stdout
    assert "--deliver" in result.stdout
    assert "--date" in result.stdout
    assert "--no-live" in result.stdout


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


# --- --date и --no-live ---


def _run_with(tmp_path: Path, *args: str) -> Result:
    return runner.invoke(app, ["run", "--product", "icao_brief", "--output", str(tmp_path), *args])


def test_run_without_date_asks_for_now_with_live_data(
    tmp_path: Path, mock_build_payload: MagicMock
) -> None:
    result = _run_with(tmp_path)

    assert result.exit_code == 0
    mock_build_payload.assert_called_once_with(query_time=None, no_live=False)
    assert "Warning" not in result.output


def test_run_no_live_alone_keeps_now(tmp_path: Path, mock_build_payload: MagicMock) -> None:
    result = _run_with(tmp_path, "--no-live")

    assert result.exit_code == 0
    mock_build_payload.assert_called_once_with(query_time=None, no_live=True)


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        (
            "2026-09-01",
            datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        ),  # дата без времени: 12:00, как в API
        ("2026-09-01T00:30", datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),
        ("2026-09-01T00:30:15", datetime(2026, 9, 1, 0, 30, 15, tzinfo=UTC)),
        ("2026-09-01T00:30Z", datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),
        ("2026-09-01T03:30+03:00", datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),
        ("2026-09-01T00:00", datetime(2026, 9, 1, 0, 0, tzinfo=UTC)),  # полночь остаётся полуночью
    ],
)
def test_run_date_is_parsed_to_utc(
    tmp_path: Path, mock_build_payload: MagicMock, argument: str, expected: datetime
) -> None:
    result = _run_with(tmp_path, "--date", argument, "--no-live")

    assert result.exit_code == 0, result.output
    mock_build_payload.assert_called_once_with(query_time=expected, no_live=True)


@pytest.mark.parametrize(
    "argument",
    [
        "2026-9-1",
        "2026/09/01",
        "yesterday",
        "20260901",
        "2026-09-01 12:00",
        "2026-09-01T12",
        "2026-09-01T12:00:00.5",
        "2026-09-01Z",
        "",
    ],
)
def test_run_rejects_malformed_date(
    tmp_path: Path, mock_build_payload: MagicMock, argument: str
) -> None:
    result = _run_with(tmp_path, "--date", argument)

    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output
    mock_build_payload.assert_not_called()


@pytest.mark.parametrize("argument", ["2026-02-30", "2026-13-01", "2026-09-01T25:00"])
def test_run_rejects_impossible_dates(
    tmp_path: Path, mock_build_payload: MagicMock, argument: str
) -> None:
    result = _run_with(tmp_path, "--date", argument)

    assert result.exit_code != 0
    assert "не календарная дата или время" in result.output
    mock_build_payload.assert_not_called()


def _hours_ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def test_run_warns_when_a_recent_date_may_mix_live_data(
    tmp_path: Path, mock_build_payload: MagicMock
) -> None:
    result = _run_with(tmp_path, "--date", _hours_ago(12))

    assert result.exit_code == 0, result.output  # предупреждение не мешает прогону
    assert "live SWPC data may be mixed in" in result.output
    assert "--no-live" in result.output
    mock_build_payload.assert_called_once()


def test_run_does_not_warn_with_no_live(tmp_path: Path, mock_build_payload: MagicMock) -> None:
    result = _run_with(tmp_path, "--date", _hours_ago(12), "--no-live")

    assert result.exit_code == 0
    assert "Warning" not in result.output


def test_run_does_not_warn_for_an_archive_date(
    tmp_path: Path, mock_build_payload: MagicMock
) -> None:
    """Старше двух суток источник live-данные не подмешивает."""
    result = _run_with(tmp_path, "--date", _hours_ago(24 * 5))

    assert result.exit_code == 0
    assert "Warning" not in result.output


# --- Момент выпуска: время данных, а не системные часы ---


def test_to_product_uses_the_moment_of_the_payload() -> None:
    from heliogeo.cli.commands.run import _to_product
    from heliogeo.llm import MockLLMProvider, generate_product

    moment = datetime(2026, 9, 1, 12, tzinfo=UTC)
    payload = UnifiedPayload(metadata=Metadata(issued_utc=moment, valid_until_utc=moment))
    result = generate_product("icao_brief", payload, MockLLMProvider(response="ok"))

    assert _to_product("icao_brief", result).metadata.issued_utc == moment


def test_to_product_without_a_moment_fails_loudly() -> None:
    from dataclasses import replace

    from heliogeo.cli.commands.run import _to_product
    from heliogeo.llm import MockLLMProvider, generate_product

    result = generate_product("icao_brief", {"metadata": {}}, MockLLMProvider(response="ok"))
    with pytest.raises(ValueError, match="issued_utc"):
        _to_product("icao_brief", replace(result, issued_utc=None))


def test_run_failure_prints_the_class_of_the_error(
    mock_build_payload: MagicMock, tmp_path: Path
) -> None:
    """Упавший шаг печатается с классом ошибки: по нему решают, повторять ли шаг."""
    from heliogeo.extractors.swx.client import SwxClientError
    from heliogeo.pipeline.builder import BuilderError

    error = BuilderError("Failed to build payload")
    error.__cause__ = SwxClientError("503", kind="unavailable")
    mock_build_payload.side_effect = error

    result = runner.invoke(app, ["run", "--product", "icao_brief", "--output", str(tmp_path)])

    assert result.exit_code == 1
    assert "Pipeline: FAILED" in result.stdout
    assert "build_payload: Failed to build payload [SOURCE_UNAVAILABLE]" in result.stdout
