"""CLI ``heliogeo corpus``: сбор, индекс, сводка, просмотр, поиск (хранилище во временном каталоге)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from heliogeo.cli.main import app
from heliogeo.core.config import get_settings
from tests.mocks.swx import QUIET_DATA, FakeSwxServer, answered_for, json_response, load_fixture

runner = CliRunner()

END_OF_DAY = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
DISCUSSION = "swx_text_discussion_2026-01-15_0030.json"
RSGA = "swx_text_rsga_2026-01-15.json"


def _issue(name: str, issued: datetime) -> httpx.Response:
    body = load_fixture(name)
    body["issued_dt"] = issued.isoformat()
    body["cycle"] = f"{issued:%H%M}"
    return json_response(body)


def _day_responses() -> list[httpx.Response | Exception]:
    return [
        json_response(answered_for(load_fixture(QUIET_DATA), END_OF_DAY)),
        _issue(RSGA, datetime(2026, 9, 1, 22, 0, tzinfo=UTC)),
        _issue(DISCUSSION, datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),
        _issue(DISCUSSION, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)),
    ]


@pytest.fixture(autouse=True)
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Хранилище команд — во временном каталоге, а не в var/objects проекта."""
    monkeypatch.setenv("OBJECT_STORE", "local")
    monkeypatch.setenv("OBJECT_STORE_DIR", str(tmp_path / "objects"))
    get_settings.cache_clear()
    yield tmp_path / "objects"
    get_settings.cache_clear()


def _backfill(monkeypatch: pytest.MonkeyPatch, *extra: str):  # type: ignore[no-untyped-def]
    FakeSwxServer(monkeypatch, _day_responses())
    return runner.invoke(
        app,
        [
            "corpus",
            "backfill",
            "--from",
            "2026-09-01",
            "--to",
            "2026-09-01",
            "--delay",
            "0",
            *extra,
        ],
    )


def test_backfill_downloads_normalizes_and_builds_the_index(
    monkeypatch: pytest.MonkeyPatch, storage: Path
) -> None:
    result = _backfill(monkeypatch)

    assert result.exit_code == 0, result.output
    assert "2026-09-01  ok" in result.output
    assert "L1: 1 суток" in result.output
    assert "Индекс корпуса: 3 выпусков" in result.output
    assert (storage / "heliogeo-lake" / "corpus" / "texts_v1.jsonl").is_file()
    assert (storage / "heliogeo-raw" / "_manifest" / "2026-09-01.json").is_file()
    assert any((storage / "heliogeo-lake" / "l1").rglob("*.parquet"))


def test_backfill_can_skip_normalization_and_index(
    monkeypatch: pytest.MonkeyPatch, storage: Path
) -> None:
    result = _backfill(monkeypatch, "--no-normalize", "--no-build")

    assert result.exit_code == 0, result.output
    assert not (storage / "heliogeo-lake").exists()


def test_backfill_rejects_bad_dates() -> None:
    bad = runner.invoke(app, ["corpus", "backfill", "--from", "вчера", "--to", "2026-09-01"])
    assert bad.exit_code != 0
    reversed_ = runner.invoke(
        app, ["corpus", "backfill", "--from", "2026-09-02", "--to", "2026-09-01"]
    )
    assert reversed_.exit_code != 0
    assert "раньше начала" in reversed_.output


def test_backfill_exits_with_error_when_the_source_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSwxServer(monkeypatch, [httpx.ConnectTimeout("лежит")])
    result = runner.invoke(
        app,
        [
            "corpus",
            "backfill",
            "--from",
            "2026-09-01",
            "--to",
            "2026-09-05",
            "--delay",
            "0",
            "--max-failures",
            "1",
        ],
    )

    assert result.exit_code == 1
    assert "Остановлено" in result.output


def test_stats_show_and_search_work_on_the_built_index(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _backfill(monkeypatch).exit_code == 0

    stats = runner.invoke(app, ["corpus", "stats", "--missing"])
    assert stats.exit_code == 0, stats.output
    assert "records: 3" in stats.output
    assert "missing_expected: 0" in stats.output

    shown = runner.invoke(app, ["corpus", "show", "2026-09-01T00:30"])
    assert shown.exit_code == 0, shown.output
    assert "discussion 0030" in shown.output
    assert "данные нужны за сутки: 2026-08-31, 2026-09-01" in shown.output
    assert "[Solar Activity]" in shown.output

    missing = runner.invoke(app, ["corpus", "show", "2026-09-01T03:00"])
    assert missing.exit_code == 1
    assert "нет в корпусе" in missing.output

    found = runner.invoke(app, ["corpus", "search", "Penticton", "--kind", "rsga", "--limit", "1"])
    assert found.exit_code == 0
    assert "Penticton" in found.output
    nothing = runner.invoke(app, ["corpus", "search", "такого нет нигде"])
    assert "ничего не найдено" in nothing.output


def test_build_rebuilds_the_index_without_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _backfill(monkeypatch, "--no-build").exit_code == 0
    offline = FakeSwxServer(monkeypatch, [httpx.ConnectTimeout("сети нет")])

    result = runner.invoke(app, ["corpus", "build"])

    assert result.exit_code == 0, result.output
    assert "выпусков в индексе: 3" in result.output
    assert offline.requests == []


def test_stats_without_an_index_explains_what_to_do() -> None:
    result = runner.invoke(app, ["corpus", "stats"])
    assert result.exit_code == 1
    assert "heliogeo corpus build" in result.output


def test_show_rejects_a_malformed_time() -> None:
    result = runner.invoke(app, ["corpus", "show", "полдень"])
    assert result.exit_code != 0
