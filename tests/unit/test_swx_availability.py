"""Фильтр «что было известно на момент запроса»: события и прогнозы «из будущего»."""

import copy
import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from heliogeo.extractors.swx.availability import (
    DAYPRE,
    THREE_DAY_FORECAST,
    AvailabilityReport,
    filter_known_at,
    parse_event_time,
    parse_issued,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MSK = timezone(timedelta(hours=3))


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


QUIET = _load("swx_snapshot_2026-09-01.json")
STORM = _load("swx_snapshot_2024-05-11.json")
NOON_QUIET = datetime(2026, 9, 1, 12, tzinfo=UTC)
NOON_STORM = datetime(2024, 5, 11, 12, tzinfo=UTC)


def _peaks(rows: list[dict[str, Any]], key: str = "max") -> list[str]:
    return [row[key][11:16] for row in rows]


# --- parse_event_time ---


def test_parse_event_time_flare_format() -> None:
    assert parse_event_time("2026-09-01T07:36:00+00:00") == datetime(2026, 9, 1, 7, 36, tzinfo=UTC)


def test_parse_event_time_cme_format() -> None:
    """Выбросы приходят как ``2026-09-01T21:24Z``: без секунд и с «Z»."""
    assert parse_event_time("2026-09-01T21:24Z") == datetime(2026, 9, 1, 21, 24, tzinfo=UTC)


def test_parse_event_time_converts_offsets_to_utc() -> None:
    parsed = parse_event_time("2026-09-01T15:00:00+03:00")

    assert parsed == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert parsed is not None
    assert parsed.tzinfo is UTC


@pytest.mark.parametrize("value", ["2026-09-01T12:00", "yesterday", "", None, 5, ["2026-09-01"]])
def test_parse_event_time_rejects_what_it_cannot_place(value: Any) -> None:
    assert parse_event_time(value) is None


# --- parse_issued ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026 Sep 01 1230 UTC", datetime(2026, 9, 1, 12, 30, tzinfo=UTC)),
        ("2024 May 11 2200 UTC", datetime(2024, 5, 11, 22, 0, tzinfo=UTC)),
        ("2026 Sep 1 0030 UTC", datetime(2026, 9, 1, 0, 30, tzinfo=UTC)),
        ("  2026 Jan 31 0000 UTC  ", datetime(2026, 1, 31, 0, 0, tzinfo=UTC)),
    ],
)
def test_parse_issued(value: str, expected: datetime) -> None:
    assert parse_issued(value) == expected


@pytest.mark.parametrize(
    ("month", "number"),
    [
        ("Jan", 1),
        ("Feb", 2),
        ("Mar", 3),
        ("Apr", 4),
        ("May", 5),
        ("Jun", 6),
        ("Jul", 7),
        ("Aug", 8),
        ("Sep", 9),
        ("Oct", 10),
        ("Nov", 11),
        ("Dec", 12),
    ],
)
def test_parse_issued_month_names_do_not_depend_on_locale(month: str, number: int) -> None:
    parsed = parse_issued(f"2026 {month} 02 0300 UTC")

    assert parsed == datetime(2026, number, 2, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "2026 Foo 01 1230 UTC",  # нет такого месяца
        "2026 Sep 31 1230 UTC",  # нет такого дня
        "2026 Sep 01 2561 UTC",  # нет такого времени
        "2026-09-01 12:30",
        "2026 Sep 01 1230",
        "",
        None,
        1230,
    ],
)
def test_parse_issued_rejects_bad_values(value: Any) -> None:
    assert parse_issued(value) is None


# --- Спокойный день 2026-09-01 на 12:00 ---


def test_quiet_day_at_noon_drops_future_flares_and_cmes() -> None:
    result, report = filter_known_at(QUIET, NOON_QUIET)

    assert _peaks(result["flares"]) == ["07:36"]
    assert [cme["time"][11:16] for cme in result["cmes"]] == ["06:00"]
    assert report.dropped_flares == 7  # в плане и руководстве назван неполный список из пяти
    assert report.dropped_cmes == 2
    assert report.dropped_events == 9
    assert report.ongoing_flares == ()


def test_quiet_day_at_noon_drops_both_forecast_products() -> None:
    """Трёхдневный прогноз выпущен в 12:30, daypre — в 22:00: на 12:00 их ещё не было."""
    result, report = filter_known_at(QUIET, NOON_QUIET)

    assert report.dropped_forecasts == (THREE_DAY_FORECAST, DAYPRE)
    fcst = result["geomag"]["fcst"]
    for key in ("raw_issued", "kp_forecast", "r_scale", "s_scale", "g_scale", "daypre_excerpt"):
        assert key not in fcst


def test_quiet_day_at_noon_keeps_aggregates_and_other_sections() -> None:
    """Агрегаты окна и таблица областей не фильтруются: часть окна из них не вычесть."""
    result, _ = filter_known_at(QUIET, NOON_QUIET)

    assert result["solar_wind"] == QUIET["solar_wind"]
    assert result["particles"] == QUIET["particles"]
    assert result["active_regions"] == QUIET["active_regions"]
    assert result["geomag"]["kp_obs"] == QUIET["geomag"]["kp_obs"]
    assert result["geomag"]["dst_obs"] == QUIET["geomag"]["dst_obs"]
    assert result["sources"] == QUIET["sources"]
    assert result["window"] == QUIET["window"]
    assert result["query_time"] == QUIET["query_time"]


# --- Бурный день 2024-05-11 на 12:00 ---


def test_storm_day_at_noon() -> None:
    result, report = filter_known_at(STORM, NOON_STORM)

    assert _peaks(result["flares"]) == ["01:23", "10:18", "10:56", "11:44"]
    assert report.dropped_flares == 7
    assert report.dropped_cmes == 2  # 16:24 и 18:00
    assert report.dropped_forecasts == (THREE_DAY_FORECAST, DAYPRE)


def test_flare_in_progress_stays_and_is_reported() -> None:
    """X1.5: начало 11:15, пик 11:44, конец 12:05. Класс известен по пику, конец не используется."""
    result, report = filter_known_at(STORM, NOON_STORM)

    assert report.ongoing_flares == ("2024-05-11T11:44:00+00:00 X1.5",)
    assert "X1.5" in [flare["class"] for flare in result["flares"]]


def test_flare_is_not_in_progress_once_it_has_ended() -> None:
    _, report = filter_known_at(STORM, datetime(2024, 5, 11, 12, 5, tzinfo=UTC))

    assert report.ongoing_flares == ()


# --- Границы ---


def test_event_exactly_at_query_time_is_kept() -> None:
    result, report = filter_known_at(QUIET, datetime(2026, 9, 1, 7, 36, tzinfo=UTC))

    assert _peaks(result["flares"]) == ["07:36"]
    assert report.dropped_flares == 7


def test_event_one_minute_after_query_time_is_dropped() -> None:
    result, report = filter_known_at(QUIET, datetime(2026, 9, 1, 7, 35, tzinfo=UTC))

    assert result["flares"] == []
    assert report.dropped_flares == 8


def test_cme_boundary() -> None:
    kept, _ = filter_known_at(QUIET, datetime(2026, 9, 1, 6, 0, tzinfo=UTC))
    dropped, report = filter_known_at(QUIET, datetime(2026, 9, 1, 5, 59, tzinfo=UTC))

    assert len(kept["cmes"]) == 1
    assert dropped["cmes"] == []
    assert report.dropped_cmes == 3


def test_forecast_boundary() -> None:
    """Трёхдневный прогноз выпущен в 12:30, daypre — в 22:00."""
    _, at_issue = filter_known_at(QUIET, datetime(2026, 9, 1, 12, 30, tzinfo=UTC))
    _, before_issue = filter_known_at(QUIET, datetime(2026, 9, 1, 12, 29, tzinfo=UTC))
    _, at_daypre = filter_known_at(QUIET, datetime(2026, 9, 1, 22, 0, tzinfo=UTC))

    assert at_issue.dropped_forecasts == (DAYPRE,)
    assert before_issue.dropped_forecasts == (THREE_DAY_FORECAST, DAYPRE)
    assert at_daypre.dropped_forecasts == ()


def test_end_of_day_drops_nothing() -> None:
    result, report = filter_known_at(QUIET, datetime(2026, 9, 1, 23, 59, tzinfo=UTC))

    assert result == QUIET
    assert report.dropped_events == 0
    assert report.dropped_forecasts == ()


def test_start_of_day_drops_every_event() -> None:
    result, report = filter_known_at(QUIET, datetime(2026, 9, 1, 0, 0, tzinfo=UTC))

    assert result["flares"] == []
    assert result["cmes"] == []
    assert (report.dropped_flares, report.dropped_cmes) == (8, 3)
    assert report.dropped_forecasts == (THREE_DAY_FORECAST, DAYPRE)


def test_query_time_in_another_timezone_is_normalized() -> None:
    in_msk, report = filter_known_at(QUIET, datetime(2026, 9, 1, 15, 0, tzinfo=MSK))
    in_utc, _ = filter_known_at(QUIET, NOON_QUIET)

    assert in_msk == in_utc
    assert report.query_time == NOON_QUIET
    assert report.query_time.tzinfo is UTC


def test_naive_query_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="часовым поясом"):
        filter_known_at(QUIET, datetime(2026, 9, 1, 12))


# --- Целостность ---


def test_input_is_not_mutated() -> None:
    original = copy.deepcopy(QUIET)

    result, _ = filter_known_at(QUIET, NOON_QUIET)

    assert original == QUIET
    assert result["flares"] is not QUIET["flares"]
    assert result["geomag"] is not QUIET["geomag"]
    assert result["geomag"]["fcst"] is not QUIET["geomag"]["fcst"]


def test_unknown_fields_survive() -> None:
    data = copy.deepcopy(QUIET)
    data["new_section"] = {"x": 1}
    data["geomag"]["fcst"]["extra"] = "kept"
    data["flares"][0]["new_field"] = "kept"

    result, _ = filter_known_at(data, NOON_QUIET)

    assert result["new_section"] == {"x": 1}
    assert result["geomag"]["fcst"]["extra"] == "kept"
    assert result["flares"][0]["new_field"] == "kept"


# --- Нестандартные ответы ---


def test_empty_response_stays_empty() -> None:
    result, report = filter_known_at({}, NOON_QUIET)

    assert result == {}
    assert report == AvailabilityReport(query_time=NOON_QUIET)


@pytest.mark.parametrize("key", ["flares", "cmes", "geomag"])
def test_null_sections_are_left_alone(key: str) -> None:
    data = {key: None}

    result, report = filter_known_at(data, NOON_QUIET)

    assert result == {key: None}
    assert report.dropped_events == 0


def test_flare_without_peak_uses_begin() -> None:
    data = {
        "flares": [{"begin": "2026-09-01T13:00:00+00:00"}, {"begin": "2026-09-01T11:00:00+00:00"}]
    }

    result, report = filter_known_at(data, NOON_QUIET)

    assert result["flares"] == [{"begin": "2026-09-01T11:00:00+00:00"}]
    assert report.dropped_flares == 1


def test_records_with_unparsable_time_are_kept() -> None:
    """Их проверит валидация payload: фильтр не прячет проблему данных."""
    data = {
        "flares": [{"max": "garbage"}, {"class": "C1.0"}, "not a mapping"],
        "cmes": [{"time": "garbage"}, {"speed": 400}, 5],
    }

    result, report = filter_known_at(data, NOON_QUIET)

    assert result == data
    assert report.dropped_events == 0


@pytest.mark.parametrize("raw_issued", ["garbage", None, 1230, ""])
def test_forecast_with_unparsable_issue_time_is_kept(raw_issued: Any) -> None:
    data = {"geomag": {"fcst": {"raw_issued": raw_issued, "g_scale": "G1", "kp_forecast": [3.0]}}}

    result, report = filter_known_at(data, NOON_QUIET)

    assert result == data
    assert report.dropped_forecasts == ()


def test_daypre_without_issue_line_is_kept() -> None:
    data = {"geomag": {"fcst": {"daypre_excerpt": [":Product: daypre", "A_Planetary 5 5 5"]}}}

    result, report = filter_known_at(data, NOON_QUIET)

    assert result == data
    assert report.dropped_forecasts == ()


def test_forecast_products_are_judged_separately() -> None:
    """Трёхдневный прогноз успел выйти (00:30), daypre — ещё нет (22:00)."""
    data = {
        "geomag": {
            "fcst": {
                "raw_issued": "2026 Sep 01 0030 UTC",
                "g_scale": "G1",
                "daypre_excerpt": [":Issued: 2026 Sep 01 2200 UTC", "A_Planetary 5 5 5"],
            }
        }
    }

    result, report = filter_known_at(data, NOON_QUIET)

    assert report.dropped_forecasts == (DAYPRE,)
    assert result["geomag"]["fcst"] == {"raw_issued": "2026 Sep 01 0030 UTC", "g_scale": "G1"}


def test_forecast_section_that_is_not_a_mapping_is_left_alone() -> None:
    data = {"geomag": {"fcst": None, "kp_obs": {"max": 2.0}}}

    result, report = filter_known_at(data, NOON_QUIET)

    assert result == data
    assert report.dropped_forecasts == ()


# --- Отчёт ---


def test_report_is_immutable() -> None:
    report = AvailabilityReport(query_time=NOON_QUIET)

    with pytest.raises(dataclasses.FrozenInstanceError):
        report.dropped_flares = 1  # type: ignore[misc]


def test_report_summary_lists_what_was_dropped() -> None:
    _, report = filter_known_at(STORM, NOON_STORM)

    summary = report.summary()

    assert "query_time=2024-05-11T12:00:00+00:00" in summary
    assert "flares=7, CMEs=2" in summary
    assert "three_day_forecast, daypre" in summary
    assert "2024-05-11T11:44:00+00:00 X1.5" in summary


def test_report_summary_of_a_clean_response_is_short() -> None:
    summary = AvailabilityReport(query_time=NOON_QUIET).summary()

    assert summary == "query_time=2026-09-01T12:00:00+00:00; dropped flares=0, CMEs=0"
