"""Типизированные ответы SWX: разбор /data и /text, проверка формы, неизвестные поля."""

import copy
import dataclasses
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from heliogeo.extractors.swx.availability import AvailabilityReport
from heliogeo.extractors.swx.response import (
    SourceRef,
    SwxDataResponse,
    SwxResponseError,
    SwxTextResponse,
    TextRef,
)
from tests.mocks.swx import (
    FETCHED_AT,
    QUIET_DATA,
    QUIET_END_OF_DAY,
    QUIET_NOON,
    QUIET_TEXT,
    STORM_DATA,
    STORM_END_OF_DAY,
    STORM_NOON,
    STORM_TEXT,
    load_fixture,
    make_data_response,
    make_text_response,
)

MSK = timezone(timedelta(hours=3))
# Момент, на который источник ответил в каждой текстовой фикстуре.
TEXT_NOON = {QUIET_TEXT: QUIET_NOON, STORM_TEXT: STORM_NOON}


def _data(*, at: datetime = QUIET_NOON, **changes: Any) -> SwxDataResponse:
    """Ответ по спокойному дню с правками верхнего уровня."""
    raw = load_fixture(QUIET_DATA)
    raw.update(changes)
    return make_data_response(raw, at=at)


def _text(**changes: Any) -> SwxTextResponse:
    raw = load_fixture(QUIET_TEXT)
    raw.update(changes)
    return make_text_response(raw)


# --- /data: реальные фикстуры ---

DATA_CASES = [
    pytest.param(
        QUIET_DATA,
        QUIET_NOON,
        datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC),
        [
            "srs",
            "events",
            "donki_cme",
            "donki_cmeanalysis",
            "dayind",
            "goes_nc_sgps",
            "omni2",
            "omni_hro_1min",
            "gfz_kp",
            "three_day_forecast",
            "daypre",
        ],
        SourceRef(
            product="srs",
            path="/opt/swx/data/warehouse/srs/2026/20260901SRS.txt",
            size_bytes=890,
            mtime=datetime(2026, 9, 22, 22, 30, 42, 302580, tzinfo=UTC),
        ),
        id="quiet",
    ),
    pytest.param(
        STORM_DATA,
        STORM_NOON,
        datetime(2024, 5, 11, 0, 0, 0, tzinfo=UTC),
        datetime(2024, 5, 11, 23, 59, 59, tzinfo=UTC),
        [
            "srs",
            "events",
            "donki_cme",
            "donki_cmeanalysis",
            "dayind",
            "goes_nc_mpsh",
            "goes_nc_sgps",
            "omni2",
            "omni_hro_1min",
            "gfz_kp",
            "three_day_forecast",
            "daypre",
        ],
        SourceRef(
            product="srs",
            path="/opt/swx/data/warehouse/srs/2024/20240511SRS.txt",
            size_bytes=1006,
            mtime=datetime(2026, 9, 22, 22, 43, 26, 547843, tzinfo=UTC),
        ),
        id="storm",
    ),
]


@pytest.mark.parametrize(
    ("name", "moment", "window_start", "window_end", "products", "first_source"), DATA_CASES
)
def test_data_response_from_a_real_fixture(
    name: str,
    moment: datetime,
    window_start: datetime,
    window_end: datetime,
    products: list[str],
    first_source: SourceRef,
) -> None:
    response = make_data_response(load_fixture(name), at=moment)

    assert response.requested_at == moment
    assert response.query_time == moment
    assert (response.window_start, response.window_end) == (window_start, window_end)
    assert response.notes == ()
    assert [source.product for source in response.sources] == products
    assert response.sources[0] == first_source
    assert response.fetched_at == FETCHED_AT
    assert response.window_spec is None
    assert response.no_live is False


@pytest.mark.parametrize(("name", "moment"), [(QUIET_DATA, QUIET_NOON), (STORM_DATA, STORM_NOON)])
def test_real_fixtures_have_no_unknown_fields_and_no_problems(name: str, moment: datetime) -> None:
    """Известные наборы ключей покрывают реальные ответы: тишина в журнале — норма."""
    response = make_data_response(load_fixture(name), at=moment)

    assert response.unknown_fields == ()
    assert response.problems == ()


@pytest.mark.parametrize(("name", "moment"), [(QUIET_DATA, QUIET_NOON), (STORM_DATA, STORM_NOON)])
def test_storage_fields_are_empty_until_the_response_is_stored(name: str, moment: datetime) -> None:
    response = make_data_response(load_fixture(name), at=moment)

    assert response.raw_sha256 is None
    assert response.raw_key is None
    assert response.from_mirror is False
    assert response.window_matches_request is None


def test_data_response_keeps_what_was_asked() -> None:
    response = SwxDataResponse.from_raw(
        load_fixture(QUIET_DATA),
        requested_at=QUIET_NOON,
        fetched_at=FETCHED_AT,
        window_spec="1d",
        no_live=True,
        raw_sha256="a" * 64,
        raw_key="swx/data/2026/09/01/1200_1d_nolive_aaaaaaaaaaaa.json",
        from_mirror=True,
    )

    assert response.window_spec == "1d"
    assert response.no_live is True
    assert response.raw_sha256 == "a" * 64
    assert response.raw_key == "swx/data/2026/09/01/1200_1d_nolive_aaaaaaaaaaaa.json"
    assert response.from_mirror is True


# --- /data: приведение к моменту запроса ---


def test_quiet_day_at_noon_drops_events_from_the_future() -> None:
    response = make_data_response(at=QUIET_NOON)

    assert response.availability.dropped_flares == 7
    assert response.availability.dropped_cmes == 2
    assert response.availability.dropped_forecasts == ("three_day_forecast", "daypre")
    assert response.events_after_query == 9
    assert len(response.data["flares"]) == 1
    assert len(response.data["cmes"]) == 1


def test_storm_day_at_noon_reports_the_flare_in_progress() -> None:
    response = make_data_response(load_fixture(STORM_DATA), at=STORM_NOON)

    assert response.events_after_query == 9
    assert response.availability.ongoing_flares == ("2024-05-11T11:44:00+00:00 X1.5",)


@pytest.mark.parametrize(
    ("name", "moment"), [(QUIET_DATA, QUIET_END_OF_DAY), (STORM_DATA, STORM_END_OF_DAY)]
)
def test_at_the_end_of_the_day_nothing_is_dropped(name: str, moment: datetime) -> None:
    raw = load_fixture(name)

    response = make_data_response(raw, at=moment)

    assert response.events_after_query == 0
    assert response.availability == AvailabilityReport(query_time=moment)
    assert response.data == raw


def test_events_after_query_is_the_availability_counter() -> None:
    response = make_data_response()

    assert response.events_after_query == response.availability.dropped_events


def test_raw_is_as_received_and_data_is_filtered() -> None:
    raw = load_fixture(QUIET_DATA)

    response = make_data_response(raw, at=QUIET_NOON)

    assert response.raw == raw
    assert len(response.raw["flares"]) == 8
    assert len(response.data["flares"]) == 1
    assert "g_scale" in response.raw["geomag"]["fcst"]
    assert "g_scale" not in response.data["geomag"]["fcst"]


def test_response_does_not_keep_references_to_the_input() -> None:
    raw = load_fixture(QUIET_DATA)
    original = copy.deepcopy(raw)

    response = make_data_response(raw, at=QUIET_END_OF_DAY)
    raw["solar_wind"]["speed"]["max"] = 0.0
    raw["flares"].clear()
    raw["sources"].append({"product": "x", "path": "y"})

    assert response.raw == original
    assert response.data == original


def test_raw_and_data_share_no_objects() -> None:
    response = make_data_response(at=QUIET_END_OF_DAY)
    original = copy.deepcopy(dict(response.raw))

    assert response.raw is not response.data
    assert response.raw["solar_wind"] is not response.data["solar_wind"]
    assert response.raw["active_regions"][0] is not response.data["active_regions"][0]
    response.data["solar_wind"]["speed"]["max"] = 0.0  # type: ignore[index]
    response.data["flares"][0]["class"] = "X99"
    assert response.raw == original


# --- /data: запрошенный момент ---


def test_requested_at_is_floored_to_the_minute_and_converted_to_utc() -> None:
    response = make_data_response(at=datetime(2026, 9, 1, 15, 0, 45, 123456, tzinfo=MSK))

    assert response.requested_at == QUIET_NOON
    assert response.requested_at.tzinfo is UTC
    assert response.availability.query_time == QUIET_NOON


def test_events_are_cut_by_the_floored_moment() -> None:
    """Источнику уходит минута, значит и отсечка по минуте: 07:36:59 даёт 07:36."""
    kept = make_data_response(at=datetime(2026, 9, 1, 7, 36, 59, tzinfo=UTC))
    dropped = make_data_response(at=datetime(2026, 9, 1, 7, 35, 59, tzinfo=UTC))

    assert len(kept.data["flares"]) == 1
    assert len(dropped.data["flares"]) == 0


def test_naive_requested_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="requested_at должен быть с часовым поясом"):
        make_data_response(at=datetime(2026, 9, 1, 12))


def test_naive_fetched_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="fetched_at должен быть с часовым поясом"):
        make_data_response(fetched_at=datetime(2026, 10, 3, 12))


def test_fetched_at_is_converted_to_utc() -> None:
    response = make_data_response(fetched_at=datetime(2026, 10, 3, 15, 0, 5, tzinfo=MSK))

    assert response.fetched_at == FETCHED_AT
    assert response.fetched_at.tzinfo is UTC


# --- /data: форма ответа ---


@pytest.mark.parametrize("body", [[], "text", None, 5, [{"query_time": "2026-09-01T12:00:00Z"}]])
def test_data_body_must_be_an_object(body: Any) -> None:
    with pytest.raises(SwxResponseError, match="/data response is not a JSON object"):
        SwxDataResponse.from_raw(body, requested_at=QUIET_NOON, fetched_at=FETCHED_AT)


@pytest.mark.parametrize(
    ("body", "missing", "received"),
    [
        ({}, "query_time, window", "none"),
        ({"query_time": "2026-09-01T12:00:00+00:00"}, "window", "query_time"),
        ({"window": {}}, "query_time", "window"),
        ({"detail": "Not found"}, "query_time, window", "detail"),
        ({"error": "boom", "status": 500}, "query_time, window", "error, status"),
    ],
)
def test_data_body_must_look_like_a_data_response(
    body: dict[str, Any], missing: str, received: str
) -> None:
    """JSON с ошибкой вместо данных нельзя принять за данные (и сохранить в зеркало)."""
    with pytest.raises(SwxResponseError) as error:
        SwxDataResponse.from_raw(body, requested_at=QUIET_NOON, fetched_at=FETCHED_AT)

    assert f"/data response has no {missing}" in str(error.value)
    assert f"keys received: {received}" in str(error.value)


def test_a_response_with_only_the_required_fields_is_accepted() -> None:
    body = {"query_time": "2026-09-01T12:00:00+00:00", "window": {}}

    response = SwxDataResponse.from_raw(body, requested_at=QUIET_NOON, fetched_at=FETCHED_AT)

    assert response.data == body
    assert response.sources == ()
    assert response.notes == ()


# --- /data: мягкий разбор ---


def test_query_time_null_is_a_problem_not_an_error() -> None:
    response = _data(query_time=None)

    assert response.query_time is None
    assert "query_time is missing" in response.problems
    assert len(response.data["flares"]) == 1  # разбор не прерван, фильтр отработал


@pytest.mark.parametrize("value", ["yesterday", "2026-09-01T12:00:00", 5, ["x"]])
def test_unparsable_query_time_is_a_problem(value: Any) -> None:
    response = _data(query_time=value)

    assert response.query_time is None
    assert any(
        problem.startswith("query_time is not an ISO 8601 time with a timezone")
        for problem in response.problems
    )


def test_window_that_is_not_an_object_is_a_problem() -> None:
    response = _data(window="ut_day")

    assert (response.window_start, response.window_end) == (None, None)
    assert "window is not an object: 'ut_day'" in response.problems


def test_window_without_start_and_end_is_a_problem() -> None:
    response = _data(window={"spec": "ut_day"})

    assert (response.window_start, response.window_end) == (None, None)
    assert "window.start is missing" in response.problems
    assert "window.end is missing" in response.problems


def test_unparsable_window_edge_is_a_problem() -> None:
    response = _data(window={"spec": "ut_day", "start": "today", "end": "2026-09-01T23:59:59Z"})

    assert response.window_start is None
    assert response.window_end == datetime(2026, 9, 1, 23, 59, 59, tzinfo=UTC)
    assert any(
        problem.startswith("window.start is not an ISO 8601") for problem in response.problems
    )


def test_notes_are_kept_as_strings() -> None:
    response = _data(notes=["OMNI gap 03:00-03:10", "kp_obs: using provisional"])

    assert response.notes == ("OMNI gap 03:00-03:10", "kp_obs: using provisional")
    assert response.problems == ()


@pytest.mark.parametrize("notes", [None, []])
def test_absent_or_empty_notes_are_an_empty_tuple(notes: Any) -> None:
    response = _data(notes=notes)

    assert response.notes == ()
    assert response.problems == ()


def test_notes_that_are_not_a_list_are_a_problem() -> None:
    response = _data(notes="all fine")

    assert response.notes == ()
    assert "notes is not a list: 'all fine'" in response.problems


def test_note_that_is_not_a_string_is_skipped_with_a_problem() -> None:
    response = _data(notes=["ok", 5, None])

    assert response.notes == ("ok",)
    assert "notes[1] is not a string: 5" in response.problems
    assert "notes[2] is not a string: None" in response.problems


def test_sources_that_are_not_a_list_are_a_problem() -> None:
    response = _data(sources={"product": "srs"})

    assert response.sources == ()
    assert any(problem.startswith("sources is not a list") for problem in response.problems)


def test_broken_source_entries_are_skipped_with_problems() -> None:
    good = {"product": "srs", "path": "/a", "bytes": 1, "mtime": "2026-09-22T22:30:42+00:00"}
    response = _data(sources=[good, "text", {"product": "events"}, {"path": "/b"}])

    assert [source.path for source in response.sources] == ["/a"]
    assert "sources[1] is not an object: 'text'" in response.problems
    assert "sources[2] has no product or path (keys: product)" in response.problems
    assert "sources[3] has no product or path (keys: path)" in response.problems


@pytest.mark.parametrize("size", [True, "890", 8.5])
def test_source_size_that_is_not_an_integer_is_dropped_with_a_problem(size: Any) -> None:
    response = _data(sources=[{"product": "srs", "path": "/a", "bytes": size}])

    assert response.sources == (SourceRef(product="srs", path="/a"),)
    assert f"sources[0].bytes is not an integer: {size!r}" in response.problems


def test_source_without_optional_fields_is_accepted() -> None:
    response = _data(sources=[{"product": "srs", "path": "/a"}])

    assert response.sources == (SourceRef(product="srs", path="/a", size_bytes=None, mtime=None),)
    assert response.problems == ()


def test_unparsable_source_mtime_is_a_problem() -> None:
    response = _data(sources=[{"product": "srs", "path": "/a", "mtime": "last week"}])

    assert response.sources[0].mtime is None
    assert any(p.startswith("sources[0].mtime is not an ISO 8601") for p in response.problems)


# --- /data: момент ответа источника ---


def test_source_answering_for_another_moment_is_a_problem() -> None:
    """Источник мог проигнорировать время: агрегаты тогда относятся не к тому моменту."""
    response = make_data_response(at=datetime(2026, 9, 1, 0, 30, tzinfo=UTC))

    assert response.problems == (
        "source answered for query_time=2026-09-01T12:00:00+00:00, "
        "but 2026-09-01T00:30:00+00:00 was requested",
    )


@pytest.mark.parametrize(
    ("minutes", "has_problem"),
    [(0, False), (1, False), (-1, False), (2, True), (-2, True)],
)
def test_query_time_tolerance_is_one_minute(minutes: int, has_problem: bool) -> None:
    response = make_data_response(at=QUIET_NOON + timedelta(minutes=minutes))

    assert bool(response.problems) is has_problem


# --- /data: неизвестные поля ---


def test_unknown_fields_are_listed_sorted_with_their_paths() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["new_section"] = {}
    raw["window"]["timezone"] = "UTC"
    raw["particles"]["xray"] = {}
    raw["solar_wind"]["temperature"] = {}
    raw["geomag"]["ap"] = 5
    raw["geomag"]["fcst"]["g_prob"] = 10
    raw["flares"][0]["new_flare_field"] = 1
    raw["cmes"][1]["analysis"] = {}
    raw["active_regions"][2]["sunspot_group"] = 3
    raw["sources"][0]["etag"] = "x"

    response = make_data_response(raw, at=QUIET_END_OF_DAY, follow_request=True)

    assert response.unknown_fields == (
        "active_regions[].sunspot_group",
        "cmes[].analysis",
        "flares[].new_flare_field",
        "geomag.ap",
        "geomag.fcst.g_prob",
        "new_section",
        "particles.xray",
        "solar_wind.temperature",
        "sources[].etag",
        "window.timezone",
    )
    assert response.problems == ()  # неизвестное поле — не проблема данных, а сведение о форме


def test_unknown_row_field_is_listed_once_however_many_rows_have_it() -> None:
    raw = load_fixture(QUIET_DATA)
    for flare in raw["flares"]:
        flare["new_flare_field"] = 1

    response = make_data_response(raw, at=QUIET_END_OF_DAY)

    assert response.unknown_fields == ("flares[].new_flare_field",)


def test_unknown_fields_are_found_in_the_raw_body_not_only_in_what_is_kept() -> None:
    """Вспышка из «будущего» отброшена, но новое поле в ней всё равно замечено."""
    raw = load_fixture(QUIET_DATA)
    raw["flares"][-1]["brand_new"] = 1

    response = make_data_response(raw, at=QUIET_NOON)

    assert response.unknown_fields == ("flares[].brand_new",)


@pytest.mark.parametrize(
    "changes",
    [
        {"particles": [1, 2]},
        {"geomag": "text"},
        {"flares": "none"},
        {"cmes": 5},
        {"solar_wind": None},
        {"sources": {"a": 1}},
    ],
)
def test_sections_of_the_wrong_shape_do_not_break_unknown_field_search(
    changes: dict[str, Any],
) -> None:
    response = _data(at=QUIET_END_OF_DAY, **changes)

    assert response.unknown_fields == ()


# --- /data: объект ---


def test_data_response_is_frozen_and_keyword_only() -> None:
    response = make_data_response()

    with pytest.raises(dataclasses.FrozenInstanceError):
        response.no_live = True  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        response.window_matches_request = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        SwxDataResponse(QUIET_NOON)  # type: ignore[misc]


def test_building_twice_gives_equal_responses() -> None:
    raw = load_fixture(QUIET_DATA)

    assert make_data_response(raw) == make_data_response(raw)


def test_source_and_text_refs_are_frozen_and_comparable() -> None:
    source = SourceRef(product="srs", path="/a")
    ref = TextRef(kind="discussion", path="/b")

    assert source == SourceRef(product="srs", path="/a")
    assert ref == TextRef(kind="discussion", path="/b", cycle=None, issued=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        source.path = "/c"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.kind = "rsga"  # type: ignore[misc]


# --- /text: реальные фикстуры ---


@pytest.mark.parametrize(
    ("name", "issued", "issued_dt", "text_length", "source_path", "source_mtime"),
    [
        pytest.param(
            QUIET_TEXT,
            "2026 Sep 01 0030 UTC",
            datetime(2026, 9, 1, 0, 30, tzinfo=UTC),
            2063,
            "/opt/swx/data/warehouse/discussion/2026/202609010030forecast_discussion.txt",
            datetime(2026, 9, 22, 22, 33, 43, 703605, tzinfo=UTC),
            id="quiet",
        ),
        pytest.param(
            STORM_TEXT,
            "2024 May 11 0030 UTC",
            datetime(2024, 5, 11, 0, 30, tzinfo=UTC),
            3400,
            "/opt/swx/data/warehouse/discussion/2024/202405110030forecast_discussion.txt",
            datetime(2026, 9, 23, 0, 29, 42, 130906, tzinfo=UTC),
            id="storm",
        ),
    ],
)
def test_text_response_from_a_real_fixture(
    name: str,
    issued: str,
    issued_dt: datetime,
    text_length: int,
    source_path: str,
    source_mtime: datetime,
) -> None:
    raw = load_fixture(name)
    response = make_text_response(raw, at=TEXT_NOON[name])

    assert response.requested_at == TEXT_NOON[name]
    assert response.query_time == TEXT_NOON[name]
    assert response.product == "forecast_discussion"
    assert response.kind == "discussion"
    assert response.cycle == "0030"
    assert response.issued == issued
    assert response.issued_dt == issued_dt
    assert response.text == raw["text"]
    assert response.text is not None
    assert len(response.text) == text_length
    assert response.text.startswith(":Product:")
    assert (response.probabilities, response.a_indices, response.f107) == (None, None, None)
    assert response.source == SourceRef(
        product="discussion", path=source_path, size_bytes=text_length, mtime=source_mtime
    )
    assert response.fetched_at == FETCHED_AT
    assert response.no_live is False
    assert (response.raw_sha256, response.raw_key, response.from_mirror) == (None, None, False)


@pytest.mark.parametrize("name", [QUIET_TEXT, STORM_TEXT])
def test_text_sections_are_parsed(name: str) -> None:
    response = make_text_response(load_fixture(name), at=TEXT_NOON[name])

    assert list(response.sections) == [
        "Solar Activity",
        "Energetic Particle",
        "Solar Wind",
        "Geospace",
    ]
    for parts in response.sections.values():
        assert sorted(parts) == ["24 hr Summary", "Forecast", "text"]
        assert all(isinstance(value, str) and value for value in parts.values())


@pytest.mark.parametrize(("name", "day"), [(QUIET_TEXT, "2026-09-01"), (STORM_TEXT, "2024-05-11")])
def test_text_available_releases_are_typed(name: str, day: str) -> None:
    response = make_text_response(load_fixture(name), at=TEXT_NOON[name])

    assert len(response.available) == 10
    assert Counter(ref.kind for ref in response.available) == {"discussion": 7, "rsga": 3}
    first = response.available[0]
    assert (first.kind, first.cycle) == ("discussion", "0030")
    assert first.issued == datetime.fromisoformat(f"{day}T00:30:00+00:00")
    assert first.path.endswith("forecast_discussion.txt")
    assert all(ref.issued is not None and ref.issued.tzinfo is UTC for ref in response.available)
    # список идёт от свежих выпусков к старым
    issued = [ref.issued for ref in response.available if ref.issued is not None]
    assert issued == sorted(issued, reverse=True)


@pytest.mark.parametrize("name", [QUIET_TEXT, STORM_TEXT])
def test_real_text_fixtures_have_no_unknown_fields_and_no_problems(name: str) -> None:
    response = make_text_response(load_fixture(name), at=TEXT_NOON[name])

    assert response.unknown_fields == ()
    assert response.problems == ()


def test_text_response_keeps_what_was_asked() -> None:
    response = SwxTextResponse.from_raw(
        load_fixture(QUIET_TEXT),
        requested_at=datetime(2026, 9, 1, 15, 0, 30, tzinfo=MSK),
        fetched_at=FETCHED_AT,
        no_live=True,
        raw_sha256="b" * 64,
        raw_key="swx/text/2026/09/01/1200_nolive_bbbbbbbbbbbb.json",
        from_mirror=True,
    )

    assert response.requested_at == QUIET_NOON
    assert response.no_live is True
    assert response.raw_sha256 == "b" * 64
    assert response.raw_key == "swx/text/2026/09/01/1200_nolive_bbbbbbbbbbbb.json"
    assert response.from_mirror is True


def test_text_response_does_not_keep_references_to_the_input() -> None:
    raw = load_fixture(QUIET_TEXT)
    original = copy.deepcopy(raw)

    response = make_text_response(raw)
    raw["available"].clear()
    raw["sections"]["Geospace"]["text"] = "changed"
    raw["text"] = "changed"

    assert response.raw == original
    assert response.sections["Geospace"]["text"] == original["sections"]["Geospace"]["text"]
    assert response.text == original["text"]
    assert len(response.available) == 10


def test_rsga_like_text_keeps_the_values_it_does_not_parse() -> None:
    """Для RSGA заполнены вероятности, индексы и F10.7; их структура не разбирается, но не теряется."""
    probabilities = {"flare_c": 25, "flare_m": 5}
    response = _text(
        kind="rsga",
        sections=None,
        probabilities=probabilities,
        a_indices=[3, 5, 2],
        f107=72,
        available=[],
    )

    assert response.kind == "rsga"
    assert response.sections == {}
    assert response.probabilities == probabilities
    assert response.probabilities is not probabilities
    assert response.a_indices == [3, 5, 2]
    assert response.f107 == 72
    assert response.available == ()
    assert response.problems == ()


# --- /text: форма и мягкий разбор ---


@pytest.mark.parametrize("body", [[], "text", None, 5])
def test_text_body_must_be_an_object(body: Any) -> None:
    with pytest.raises(SwxResponseError, match="/text response is not a JSON object"):
        SwxTextResponse.from_raw(body, requested_at=QUIET_NOON, fetched_at=FETCHED_AT)


@pytest.mark.parametrize(
    ("body", "received"),
    [({}, "none"), ({"detail": "Not found"}, "detail"), ({"text": "x"}, "text")],
)
def test_text_body_must_have_query_time(body: dict[str, Any], received: str) -> None:
    with pytest.raises(SwxResponseError) as error:
        SwxTextResponse.from_raw(body, requested_at=QUIET_NOON, fetched_at=FETCHED_AT)

    assert "/text response has no query_time" in str(error.value)
    assert f"keys received: {received}" in str(error.value)


def test_text_response_with_only_query_time_is_accepted() -> None:
    response = SwxTextResponse.from_raw(
        {"query_time": "2026-09-01T12:00:00+00:00"}, requested_at=QUIET_NOON, fetched_at=FETCHED_AT
    )

    assert response.kind is None
    assert response.sections == {}
    assert response.available == ()
    assert response.problems == ()


def test_naive_times_are_rejected_in_text_response() -> None:
    with pytest.raises(ValueError, match="requested_at должен быть с часовым поясом"):
        make_text_response(at=datetime(2026, 9, 1, 12))
    with pytest.raises(ValueError, match="fetched_at должен быть с часовым поясом"):
        make_text_response(fetched_at=datetime(2026, 10, 3, 12))


def test_text_source_answering_for_another_moment_is_a_problem() -> None:
    response = make_text_response(at=datetime(2026, 9, 1, 0, 30, tzinfo=UTC))

    assert response.problems == (
        "source answered for query_time=2026-09-01T12:00:00+00:00, "
        "but 2026-09-01T00:30:00+00:00 was requested",
    )


def test_unknown_text_kind_is_a_problem() -> None:
    response = _text(kind="summary")

    assert response.kind == "summary"
    assert response.problems == ("unknown text kind: 'summary'",)


def test_issued_and_issued_dt_must_mean_the_same_moment() -> None:
    response = _text(issued="2026 Sep 01 1230 UTC")

    assert any("are different moments" in problem for problem in response.problems)


def test_unparsable_issued_dt_is_a_problem() -> None:
    response = _text(issued_dt="soon")

    assert response.issued_dt is None
    assert any(p.startswith("issued_dt is not an ISO 8601") for p in response.problems)


def test_unparsable_issued_string_is_not_compared() -> None:
    response = _text(issued="the other day")

    assert response.issued == "the other day"
    assert response.problems == ()


@pytest.mark.parametrize("field", ["product", "kind", "cycle", "issued", "text"])
def test_text_fields_that_are_not_strings_are_problems(field: str) -> None:
    response = _text(**{field: 30})

    assert getattr(response, field) is None
    assert f"{field} is not a string: 30" in response.problems


def test_sections_that_are_not_an_object_are_a_problem() -> None:
    response = _text(sections=["Solar Wind"])

    assert response.sections == {}
    assert "sections is not an object: list" in response.problems


def test_broken_sections_are_skipped_with_problems() -> None:
    response = _text(
        sections={
            "Solar Wind": {"text": "calm", "Forecast": 5},
            "Geospace": "quiet",
        }
    )

    assert response.sections == {"Solar Wind": {"text": "calm"}}
    assert "sections['Solar Wind']['Forecast'] is not a string" in response.problems
    assert "sections['Geospace'] is not an object" in response.problems


def test_available_that_is_not_a_list_is_a_problem() -> None:
    response = _text(available={"kind": "discussion"})

    assert response.available == ()
    assert any(problem.startswith("available is not a list") for problem in response.problems)


def test_broken_available_entries_are_skipped_with_problems() -> None:
    response = _text(
        available=[
            {
                "kind": "discussion",
                "cycle": "0030",
                "issued": "2026-09-01T00:30:00+00:00",
                "path": "/a",
            },
            "text",
            {"kind": "rsga"},
            {"kind": "discussion", "cycle": 1230, "issued": "tomorrow", "path": "/b"},
        ]
    )

    assert [ref.path for ref in response.available] == ["/a", "/b"]
    assert response.available[1] == TextRef(kind="discussion", path="/b", cycle=None, issued=None)
    assert "available[1] is not an object: 'text'" in response.problems
    assert "available[2] has no kind or path (keys: kind)" in response.problems
    assert "available[3].cycle is not a string: 1230" in response.problems
    assert any(p.startswith("available[3].issued is not an ISO 8601") for p in response.problems)


def test_broken_text_source_is_a_problem() -> None:
    response = _text(source={"product": "discussion"})

    assert response.source is None
    assert "source has no product or path (keys: product)" in response.problems


def test_absent_text_source_is_fine() -> None:
    response = _text(source=None)

    assert response.source is None
    assert response.problems == ()


def test_unknown_text_fields_are_listed_with_their_paths() -> None:
    raw = load_fixture(QUIET_TEXT)
    raw["revision"] = 3
    raw["source"]["etag"] = "x"
    raw["available"][0]["size"] = 10
    raw["available"][4]["size"] = 11

    response = make_text_response(raw)

    assert response.unknown_fields == ("available[].size", "revision", "source.etag")


def test_text_response_is_frozen_and_keyword_only() -> None:
    response = make_text_response()

    with pytest.raises(dataclasses.FrozenInstanceError):
        response.text = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        SwxTextResponse(QUIET_NOON)  # type: ignore[misc]
