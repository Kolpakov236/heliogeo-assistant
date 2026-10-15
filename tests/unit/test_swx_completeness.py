"""Полнота ответа ``/data``: что нужно для выпуска и признаков и чего в ответе не хватает."""

import copy
from typing import Any

import pytest

from heliogeo.extractors.swx.completeness import (
    CRITICAL_FIELDS,
    EXPECTED_PRODUCTS,
    IMPORTANT_FIELDS,
    check_completeness,
)
from tests.mocks.swx import (
    QUIET_DATA,
    QUIET_END_OF_DAY,
    STORM_DATA,
    STORM_END_OF_DAY,
    load_fixture,
    make_data_response,
)


def _check(raw: dict[str, Any] | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
    return check_completeness(make_data_response(raw, at=QUIET_END_OF_DAY, **kwargs))


def test_both_reference_days_are_complete() -> None:
    quiet = _check()
    storm = check_completeness(
        make_data_response(load_fixture(STORM_DATA), at=STORM_END_OF_DAY, no_live=True)
    )

    for result in (quiet, storm):
        assert result.critical == ()
        assert result.important == ()
        assert result.products == ()
        assert result.has_critical_gaps is False
        assert result.notes() == []


def test_an_empty_event_list_is_a_quiet_day_but_a_missing_one_is_unknown() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["flares"] = []
    assert _check(raw).critical == ()

    del raw["flares"]
    assert _check(raw).critical == ("flares",)

    raw["flares"] = None
    assert _check(raw).critical == ("flares",)


@pytest.mark.parametrize("path", CRITICAL_FIELDS)
def test_every_critical_field_is_noticed_when_it_goes_missing(path: str) -> None:
    raw = load_fixture(QUIET_DATA)
    node = raw
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = None

    result = _check(raw)

    assert path in result.critical
    assert result.has_critical_gaps is True
    assert any(path in note for note in result.notes())


@pytest.mark.parametrize("path", IMPORTANT_FIELDS)
def test_every_important_field_is_noticed_when_it_goes_missing(path: str) -> None:
    raw = load_fixture(QUIET_DATA)
    node = raw
    *parents, last = path.split(".")
    for part in parents:
        node = node[part]
    node[last] = None

    result = _check(raw)

    assert path in result.important
    assert result.critical == ()


def test_a_whole_section_missing_reports_every_field_inside_it() -> None:
    raw = load_fixture(QUIET_DATA)
    del raw["geomag"]

    result = _check(raw)

    assert "geomag.kp_obs.max" in result.critical
    assert {"geomag.dst_obs", "geomag.dst_min_day", "geomag.fcst.raw_issued"} <= set(
        result.important
    )


def test_zero_is_a_value_and_not_a_gap() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["geomag"]["dst_obs"] = 0
    raw["geomag"]["kp_obs"]["max"] = 0.0

    result = _check(raw)

    assert "geomag.kp_obs.max" not in result.critical
    assert "geomag.dst_obs" not in result.important


def test_missing_products_are_listed_and_synonyms_are_accepted() -> None:
    raw = load_fixture(QUIET_DATA)
    raw["sources"] = [s for s in raw["sources"] if s["product"] not in ("srs", "daypre")]
    assert _check(raw).products == ("srs", "daypre")

    raw = load_fixture(QUIET_DATA)
    raw["sources"] = [s for s in raw["sources"] if s["product"] != "omni_hro_1min"]
    assert _check(raw).products == ()  # есть omni2: почасовой ряд заменяет минутный

    raw["sources"] = [s for s in raw["sources"] if s["product"] != "omni2"]
    assert _check(raw).products == ("omni_hro_1min|omni2",)


def test_a_forecast_issued_after_the_request_counts_as_missing() -> None:
    """Прогноз 12:30 на запрос 12:00 источнику «из будущего»: его нет в приведённом ответе."""
    response = make_data_response(load_fixture(QUIET_DATA), no_live=True)  # момент запроса 12:00

    assert "geomag.fcst.raw_issued" in check_completeness(response).important


def test_the_check_does_not_change_the_response() -> None:
    response = make_data_response(at=QUIET_END_OF_DAY, no_live=True)
    before = copy.deepcopy(dict(response.data))

    check_completeness(response)

    assert dict(response.data) == before


def test_the_field_lists_do_not_overlap() -> None:
    assert not set(CRITICAL_FIELDS) & set(IMPORTANT_FIELDS)
    assert len({p for group in EXPECTED_PRODUCTS for p in group}) == sum(
        len(group) for group in EXPECTED_PRODUCTS
    )
