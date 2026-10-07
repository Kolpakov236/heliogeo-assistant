"""Тесты SwxExtractor: сборка UnifiedPayload из типизированных ответов клиента."""

import copy
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from heliogeo.extractors.swx.client import SwxClient, SwxClientError
from heliogeo.extractors.swx.extractor import SwxExtractor, SwxExtractorError
from heliogeo.extractors.swx.response import SwxTextResponse
from heliogeo.schema import UnifiedPayload
from tests.mocks.swx import (
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_NOON,
    STORM_DATA,
    STORM_NOON,
    load_fixture,
    make_data_response,
    make_text_response,
)

EXTRACTOR_LOGGER = "heliogeo.extractors.swx.extractor"


@pytest.fixture
def client() -> SwxClient:
    """Mock-клиент (URL не используется: get_data и get_text подменяются)."""
    return SwxClient(base_url="https://test.local")


# --- Сборка payload ---


def test_extract_quiet_returns_payload(client: SwxClient) -> None:
    """Спокойный день → валидный UnifiedPayload (на конец суток: ничего не отброшено)."""
    extractor = SwxExtractor(client=client)
    response = make_data_response(at=QUIET_END_OF_DAY, follow_request=True)
    with patch.object(client, "get_data", return_value=response):
        payload, text = extractor.extract(query_time=QUIET_END_OF_DAY)

    assert isinstance(payload, UnifiedPayload)
    assert text is None
    assert len(payload.solar_activity.active_regions) == 6
    assert len(payload.solar_activity.flares_last_24h) == 8
    assert len(payload.solar_activity.cme_list) == 3
    assert payload.geospace.kp_observed == 2.333
    assert payload.geospace.current_level == "quiet"


def test_extract_storm_returns_payload(client: SwxClient) -> None:
    """Бурный день → storm."""
    extractor = SwxExtractor(client=client)
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_NOON)
    with patch.object(client, "get_data", return_value=response):
        payload, _ = extractor.extract(query_time=STORM_NOON)

    assert payload.geospace.current_level == "storm"
    assert payload.geospace.kp_observed is not None
    assert payload.geospace.kp_observed >= 5


def test_extract_with_text(client: SwxClient) -> None:
    """include_text=True → возвращает и типизированный текст."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", return_value=make_data_response()),
        patch.object(client, "get_text", return_value=make_text_response()) as get_text,
    ):
        payload, text = extractor.extract(query_time=QUIET_NOON, include_text=True)

    assert isinstance(payload, UnifiedPayload)
    assert isinstance(text, SwxTextResponse)
    assert text.kind == "discussion"
    assert get_text.call_args.kwargs == {"query_time": QUIET_NOON, "no_live": False}


def test_extract_text_failure_non_fatal(client: SwxClient) -> None:
    """Ошибка /text не ломает extract."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", return_value=make_data_response()),
        patch.object(client, "get_text", side_effect=SwxClientError("text unavailable")),
    ):
        payload, text = extractor.extract(include_text=True)

    assert isinstance(payload, UnifiedPayload)
    assert text is None


def test_extract_snapshot_failure(client: SwxClient) -> None:
    """Ошибка /data → SwxExtractorError."""
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", side_effect=SwxClientError("boom")),
        pytest.raises(SwxExtractorError, match="Failed to fetch snapshot"),
    ):
        extractor.extract()

    assert extractor.last_response is None


def test_extract_technical_issues_omni(client: SwxClient) -> None:
    """source=omni → technical_issues не пусто."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response()):
        payload, _ = extractor.extract()

    assert payload.technical_issues is not None
    assert "omni_hro_1min" in payload.technical_issues


def test_extract_notes_of_the_source_go_to_technical_issues(client: SwxClient) -> None:
    raw = load_fixture(QUIET_DATA)
    raw["notes"] = ["OMNI gap 03:00-03:10"]
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(raw)):
        payload, _ = extractor.extract()

    assert payload.technical_issues is not None
    assert payload.technical_issues.startswith("OMNI gap 03:00-03:10 | ")


def test_extract_window_passed(client: SwxClient) -> None:
    """window и no_live передаются в клиент."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response()) as get_data:
        extractor.extract(window="15m", no_live=True)

    assert get_data.call_args.kwargs["window"] == "15m"
    assert get_data.call_args.kwargs["no_live"] is True


def test_extract_validation_failure(client: SwxClient) -> None:
    """Битые данные → SwxExtractorError."""
    extractor = SwxExtractor(client=client)
    bad_data = {
        "query_time": "2026-09-01T12:00:00+00:00",
        "window": {
            "spec": "ut_day",
            "start": "2026-09-01T00:00:00+00:00",
            "end": "2026-09-01T23:59:59+00:00",
        },
        "active_regions": [],
        "flares": [],
        "cmes": [],
        "particles": {},
        "solar_wind": {},
        "geomag": {"kp_obs": {"max": 99.0}},  # Kp > 9 → validation fail
        "notes": [],
    }
    with (
        patch.object(client, "get_data", return_value=make_data_response(bad_data)),
        pytest.raises(SwxExtractorError, match="Payload validation failed"),
    ):
        extractor.extract()


def test_extract_unparsable_query_time_fails_loudly(client: SwxClient) -> None:
    """Нет момента ответа — нет issued_utc: payload не собирается молча с выдуманным временем."""
    raw = load_fixture(QUIET_DATA)
    raw["query_time"] = "yesterday"
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", return_value=make_data_response(raw)),
        pytest.raises(SwxExtractorError, match="Payload validation failed"),
    ):
        extractor.extract()


def test_extract_default_client() -> None:
    """Без client → создаётся автоматически."""
    extractor = SwxExtractor()
    assert extractor._client is not None
    assert isinstance(extractor._client, SwxClient)


def test_extract_now_default(client: SwxClient) -> None:
    """query_time=None → «сейчас» по часам клиента, с точностью до минуты."""
    extractor = SwxExtractor(client=client)
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    with patch.object(client, "get_data", return_value=make_data_response()) as get_data:
        payload, _ = extractor.extract()

    asked = get_data.call_args.kwargs["query_time"]
    assert isinstance(payload, UnifiedPayload)
    assert asked.tzinfo is not None
    assert before <= asked <= datetime.now(UTC)


def test_extract_rejects_naive_query_time(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", return_value=make_data_response()) as get_data,
        pytest.raises(ValueError, match="часовым поясом"),
    ):
        extractor.extract(query_time=datetime(2026, 9, 1, 12))

    get_data.assert_not_called()


def test_extract_asks_the_client_for_the_requested_moment(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    moment = datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    with patch.object(client, "get_data", return_value=make_data_response()) as get_data:
        extractor.extract(query_time=moment, no_live=True)

    assert get_data.call_args.kwargs["query_time"] == moment
    assert get_data.call_args.kwargs["no_live"] is True


# --- Payload строится из data (то, что было известно на момент запроса), а не из raw ---


def test_payload_is_built_from_what_was_known_at_the_requested_moment(client: SwxClient) -> None:
    """На 12:00 в payload остаются только вспышка 07:36 и выброс 06:00."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(at=QUIET_NOON)):
        payload, _ = extractor.extract(query_time=QUIET_NOON)

    assert [flare.time.strftime("%H:%M") for flare in payload.solar_activity.flares_last_24h] == [
        "07:36"
    ]
    assert [cme.time.strftime("%H:%M") for cme in payload.solar_activity.cme_list] == ["06:00"]


def test_payload_forecast_products_depend_on_the_moment(client: SwxClient) -> None:
    """Прогноз на сутки выпущен в 12:30, daypre — в 22:00: на 12:00 их не было, к вечеру были."""
    # В реальных ответах сетка Kp пуста; здесь она задана, чтобы увидеть, что отсечка по моменту
    # действует на то, из чего строится прогноз в payload.
    raw = load_fixture(QUIET_DATA)
    raw["geomag"]["fcst"]["kp_forecast"] = [1.67, 5.0, 3.67]
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(raw, at=QUIET_NOON)):
        at_noon, _ = extractor.extract(query_time=QUIET_NOON)
    evening = make_data_response(raw, at=QUIET_END_OF_DAY, follow_request=True)
    with patch.object(client, "get_data", return_value=evening):
        at_end_of_day, _ = extractor.extract(query_time=QUIET_END_OF_DAY)

    assert at_noon.forecast.geomagnetic_g is None
    assert at_noon.geospace.kp_fcst is None
    assert at_end_of_day.geospace.kp_fcst == 5.0
    assert at_end_of_day.forecast.geomagnetic_g == "G1"


def test_payload_does_not_take_the_g_scale_text_field_of_the_source(client: SwxClient) -> None:
    """Источник пишет g_scale=G1 для спокойного дня (Kp 1–2 в прогнозе): в payload шкалы нет."""
    extractor = SwxExtractor(client=client)
    response = make_data_response(at=QUIET_END_OF_DAY, follow_request=True)
    assert response.data["geomag"]["fcst"]["g_scale"] == "G1"
    with patch.object(client, "get_data", return_value=response):
        payload, _ = extractor.extract(query_time=QUIET_END_OF_DAY)

    assert payload.forecast.geomagnetic_g is None


def test_payload_keeps_aggregates_for_the_whole_window(client: SwxClient) -> None:
    """Агрегаты окна не режутся по моменту: из них нельзя вычесть часть окна."""
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(at=QUIET_NOON)):
        payload, _ = extractor.extract(query_time=QUIET_NOON)

    assert payload.solar_wind.speed_max == 449.6
    assert payload.geospace.kp_observed == 2.333
    assert len(payload.solar_activity.active_regions) == 6


def test_storm_day_at_noon(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_NOON)
    with patch.object(client, "get_data", return_value=response):
        payload, _ = extractor.extract(query_time=STORM_NOON)

    assert len(payload.solar_activity.flares_last_24h) == 4
    assert len(payload.solar_activity.cme_list) == 3
    assert payload.geospace.current_level == "storm"


def test_payload_metadata_comes_from_the_typed_response(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    response = make_data_response()
    with patch.object(client, "get_data", return_value=response):
        payload, _ = extractor.extract()

    assert payload.metadata.issued_utc == response.query_time
    assert payload.metadata.valid_until_utc == response.window_end
    assert payload.metadata.valid_until_utc == datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC)


def test_payload_validity_falls_back_to_the_answered_moment_without_a_window_end(
    client: SwxClient,
) -> None:
    raw = load_fixture(QUIET_DATA)
    raw["window"] = {"spec": "ut_day"}
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=make_data_response(raw)):
        payload, _ = extractor.extract()

    assert payload.metadata.valid_until_utc == QUIET_NOON


def test_extract_does_not_modify_the_response(client: SwxClient) -> None:
    response = make_data_response(at=QUIET_NOON)
    raw_before = copy.deepcopy(dict(response.raw))
    data_before = copy.deepcopy(dict(response.data))
    extractor = SwxExtractor(client=client)
    with patch.object(client, "get_data", return_value=response):
        extractor.extract(query_time=QUIET_NOON)

    assert response.raw == raw_before
    assert response.data == data_before


# --- Что осталось от последнего вызова ---


def test_extract_exposes_the_last_response_and_its_availability(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    assert extractor.last_response is None
    assert extractor.last_availability is None

    response = make_data_response(at=QUIET_NOON)
    with patch.object(client, "get_data", return_value=response):
        extractor.extract(query_time=QUIET_NOON)

    assert extractor.last_response is response
    assert extractor.last_availability is response.availability
    assert extractor.last_availability is not None
    assert (
        extractor.last_availability.dropped_flares,
        extractor.last_availability.dropped_cmes,
    ) == (
        7,
        2,
    )
    assert extractor.last_availability.dropped_forecasts == ("three_day_forecast", "daypre")


def test_extract_logs_what_was_dropped(client: SwxClient, caplog: pytest.LogCaptureFixture) -> None:
    extractor = SwxExtractor(client=client)
    caplog.set_level(logging.INFO, logger=EXTRACTOR_LOGGER)
    with patch.object(client, "get_data", return_value=make_data_response(at=QUIET_NOON)):
        extractor.extract(query_time=QUIET_NOON)

    assert "Availability: query_time=2026-09-01T12:00:00+00:00; dropped flares=7, CMEs=2" in (
        caplog.text
    )


def test_extract_after_a_failure_keeps_the_previous_response(client: SwxClient) -> None:
    extractor = SwxExtractor(client=client)
    response = make_data_response()
    with patch.object(client, "get_data", return_value=response):
        extractor.extract()
    with (
        patch.object(client, "get_data", side_effect=SwxClientError("boom")),
        pytest.raises(SwxExtractorError),
    ):
        extractor.extract()

    assert extractor.last_response is response


def test_extract_now_is_decided_once_by_the_clock_of_the_client() -> None:
    """«Сейчас» задают часы клиента, один раз: и /data, и /text спрашивают один и тот же момент."""
    ticks = iter(
        [
            datetime(2026, 9, 1, 12, 0, 10, tzinfo=UTC),
            datetime(2026, 9, 1, 12, 7, 55, tzinfo=UTC),
        ]
    )
    client = SwxClient(base_url="https://test.local", clock=lambda: next(ticks))
    extractor = SwxExtractor(client=client)
    with (
        patch.object(client, "get_data", return_value=make_data_response()) as get_data,
        patch.object(client, "get_text", return_value=make_text_response()) as get_text,
    ):
        extractor.extract(include_text=True)

    assert get_data.call_args.kwargs["query_time"] == QUIET_NOON
    assert get_text.call_args.kwargs["query_time"] == QUIET_NOON


def test_extract_does_not_read_the_system_clock(client: SwxClient) -> None:
    """С явным моментом извлекатель не обращается к часам вообще: прогон по архиву воспроизводим."""

    def forbidden_clock() -> datetime:
        raise AssertionError("системные часы прочитаны")

    extractor = SwxExtractor(client=SwxClient(base_url="https://test.local", clock=forbidden_clock))
    with patch.object(extractor._client, "get_data", return_value=make_data_response()) as get_data:
        payload, _ = extractor.extract(query_time=QUIET_NOON)

    assert payload.metadata.issued_utc == QUIET_NOON
    assert get_data.call_args.kwargs["query_time"] == QUIET_NOON
