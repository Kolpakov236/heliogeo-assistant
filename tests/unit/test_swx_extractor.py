"""Тесты SwxExtractor."""

import copy
import json
import logging
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
    """Спокойный день → валидный UnifiedPayload (на конец суток: ничего не отброшено)."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, text = extractor.extract(query_time=datetime(2026, 9, 1, 23, 59, tzinfo=UTC))

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


def test_extract_with_text(client: SwxClient, snapshot_quiet: dict, text_quiet: dict) -> None:
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


def test_extract_text_failure_non_fatal(client: SwxClient, snapshot_quiet: dict) -> None:
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


def test_extract_validation_failure(client: SwxClient) -> None:
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
    with (
        patch.object(client, "get_snapshot", return_value=bad_data),
        pytest.raises(SwxExtractorError, match="Payload validation failed"),
    ):
        extractor.extract()


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


# --- Приведение ответа к моменту запроса ---


def test_extract_at_noon_drops_events_from_the_future(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    """На 12:00 в payload остаются только вспышка 07:36 и выброс 06:00."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert [flare.time.strftime("%H:%M") for flare in payload.solar_activity.flares_last_24h] == [
        "07:36"
    ]
    assert [cme.time.strftime("%H:%M") for cme in payload.solar_activity.cme_list] == ["06:00"]


def test_extract_at_noon_drops_forecast_products_from_the_future(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    """Прогноз на сутки выпущен в 12:30, daypre — в 22:00: на 12:00 их не было."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert payload.forecast.geomagnetic_g is None
    assert payload.geospace.kp_fcst is None


def test_extract_at_end_of_day_keeps_forecast_products(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 23, 59, tzinfo=UTC))

    assert payload.forecast.geomagnetic_g == "G1"


def test_extract_keeps_aggregates_for_the_whole_window(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    """Агрегаты окна не режутся по query_time: из них нельзя вычесть часть окна."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        payload, _ = extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert payload.solar_wind.speed_max == 449.6
    assert payload.geospace.kp_observed == 2.333
    assert len(payload.solar_activity.active_regions) == 6


def test_extract_storm_day_at_noon(client: SwxClient, snapshot_storm: dict) -> None:
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_storm):
        payload, _ = extractor.extract(query_time=datetime(2024, 5, 11, 12, tzinfo=UTC))

    assert len(payload.solar_activity.flares_last_24h) == 4
    assert len(payload.solar_activity.cme_list) == 3
    assert payload.geospace.current_level == "storm"


def test_extract_exposes_the_availability_report(client: SwxClient, snapshot_quiet: dict) -> None:
    extractor = SwxExtractor(client=client)
    assert extractor.last_availability is None

    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    report = extractor.last_availability
    assert report is not None
    assert report.query_time == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert (report.dropped_flares, report.dropped_cmes) == (7, 2)
    assert report.dropped_forecasts == ("three_day_forecast", "daypre")


def test_extract_does_not_modify_the_client_response(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    original = copy.deepcopy(snapshot_quiet)
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert snapshot_quiet == original


def test_extract_asks_the_client_for_the_requested_moment(
    client: SwxClient, snapshot_quiet: dict
) -> None:
    extractor = SwxExtractor(client=client)
    moment = datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet) as get:
        extractor.extract(query_time=moment, no_live=True)

    assert get.call_args.kwargs["query_time"] == moment
    assert get.call_args.kwargs["no_live"] is True


def test_extract_rejects_naive_query_time(client: SwxClient, snapshot_quiet: dict) -> None:
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_snapshot", return_value=snapshot_quiet),
        pytest.raises(ValueError, match="часовым поясом"),
    ):
        extractor.extract(query_time=datetime(2026, 9, 1, 12))


def test_extract_logs_what_was_dropped(
    client: SwxClient, snapshot_quiet: dict, caplog: pytest.LogCaptureFixture
) -> None:
    extractor = SwxExtractor(client=client)
    caplog.set_level(logging.INFO, logger="heliogeo.extractors.swx.extractor")
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert "Availability: query_time=2026-09-01T12:00:00+00:00; dropped flares=7, CMEs=2" in (
        caplog.text
    )


def test_extract_warns_when_the_source_answers_for_another_moment(
    client: SwxClient, snapshot_quiet: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """Источник мог проигнорировать время в запросе: агрегаты тогда относятся не к тому моменту."""
    extractor = SwxExtractor(client=client)
    caplog.set_level(logging.WARNING, logger="heliogeo.extractors.swx.extractor")
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        extractor.extract(query_time=datetime(2026, 9, 1, 0, 30, tzinfo=UTC))

    assert "Source answered for query_time=2026-09-01T12:00:00+00:00" in caplog.text
    assert "2026-09-01T00:30:00+00:00 was requested" in caplog.text


def test_extract_does_not_warn_when_the_moments_match(
    client: SwxClient, snapshot_quiet: dict, caplog: pytest.LogCaptureFixture
) -> None:
    extractor = SwxExtractor(client=client)
    caplog.set_level(logging.WARNING, logger="heliogeo.extractors.swx.extractor")
    with patch.object(client, "get_snapshot", return_value=snapshot_quiet):
        extractor.extract(query_time=datetime(2026, 9, 1, 12, tzinfo=UTC))

    assert "Source answered" not in caplog.text
