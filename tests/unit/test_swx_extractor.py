"""Тесты SwxExtractor."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.extractor import SwxExtractor, SwxExtractorError
from heliogeo.schema import UnifiedPayload

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def snapshot_quiet() -> dict:
    """Спокойный день 2026-09-01."""
    path = FIXTURES_DIR / "swx_snapshot_2026-09-01.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def snapshot_storm() -> dict:
    """Бурный день 2024-05-11."""
    path = FIXTURES_DIR / "swx_snapshot_2024-05-11.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def text_quiet() -> dict:
    """Forecast Discussion 2026-09-01."""
    path = FIXTURES_DIR / "swx_text_2026-09-01.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def client() -> SwxClient:
    """Mock-клиент."""
    return SwxClient(base_url="https://test.local")


def test_extract_quiet_returns_payload(client: SwxClient, snapshot_quiet: dict) -> None:
    """Спокойный день → валидный UnifiedPayload."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, text = extractor.extract(
            query_time=datetime(2026, 9, 1, 12, tzinfo=UTC)
        )

    assert isinstance(payload, UnifiedPayload)
    assert text is None
    assert len(payload.solar_activity.active_regions) == 6
    assert len(payload.solar_activity.flares_last_24h) == 8
    assert len(payload.solar_activity.cme_list) == 3
    assert payload.geospace.kp_observed == 2.333
    assert payload.geospace.current_level == "quiet"


def test_extract_storm_returns_payload(client: SwxClient, snapshot_storm: dict) -> None:
    """Бурный день → storm."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_storm):
        payload, _ = extractor.extract(query_time=datetime(2024, 5, 11, 12, tzinfo=UTC))

    assert payload.geospace.current_level == "storm"
    assert payload.geospace.kp_observed is not None
    assert payload.geospace.kp_observed >= 5


def test_extract_with_text(
    client: SwxClient, snapshot_quiet: dict, text_quiet: dict
) -> None:
    """include_text=True → возвращает и текст."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_snapshot", return_value=snapshot_quiet),
        patch.object(client, "get_text", return_value=text_quiet),
    ):
        payload, text = extractor.extract(
            query_time=datetime(2026, 9, 1, 12, tzinfo=UTC),
            include_text=True,
        )

    assert isinstance(payload, UnifiedPayload)
    assert text is not None
    assert text["kind"] == "discussion"


def test_extract_text_failure_non_fatal(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    """Ошибка /text не ломает extract."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_snapshot", return_value=snapshot_quiet),
        patch.object(
            client,
            "get_text",
            side_effect=SwxClientError("text unavailable"),
        ),
    ):
        payload, text = extractor.extract(include_text=True)

    assert isinstance(payload, UnifiedPayload)
    assert text is None


def test_extract_snapshot_failure(client: SwxClient) -> None:
    """Ошибка /data → SwxExtractorError."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(
            client,
            "get_snapshot",
            side_effect=SwxClientError("boom"),
        ),
        pytest.raises(SwxExtractorError, match="Failed to fetch snapshot"),
    ):
        extractor.extract()


def test_extract_technical_issues_omni(client: SwxClient, snapshot_quiet: dict) -> None:
    """source=omni → technical_issues не пусто."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract()

    assert payload.technical_issues is not None
    assert "omni_hro_1min" in payload.technical_issues


def test_extract_window_passed(client: SwxClient, snapshot_quiet: dict) -> None:
    """window передаётся в клиент."""
    extractor = SwxExtractor(client=client)
    captured: dict = {}

    def fake_get_snapshot(**kwargs: object) -> dict:
        captured.update(kwargs)
        return snapshot_quiet

    with patch.object(client, "get_snapshot", side_effect=fake_get_snapshot):
        extractor.extract(window="15m", no_live=True)

    assert captured["window"] == "15m"
    assert captured["no_live"] is True


def test_extract_validation_invalid_data(client: SwxClient) -> None:
    """Битые данные → SwxExtractorError."""
    extractor = SwxExtractor(client=client)
    bad_data = {
        "query_time": "2026-09-01T12:00:00+00:00",
        "active_regions": [],
        "flares": [],
        "cmes": [],
        "particles": {},
        "solar_wind": {},
        "geomag": {"kp_obs": {"max": 99.0}},  # Kp > 9 → validation fail
        "notes": [],
    }
    with (patch.object(client, "get_snapshot", return_value=bad_data),):
        payload, _ = extractor.extract()
        assert payload.geospace.kp_observed == 9


def test_extract_default_client() -> None:
    """Без client → создаётся автоматически."""
    extractor = SwxExtractor()
    assert extractor._client is not None
    assert isinstance(extractor._client, SwxClient)


def test_extract_now_default(client: SwxClient, snapshot_quiet: dict) -> None:
    """query_time=None → используется now."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract()

    assert isinstance(payload, UnifiedPayload)
