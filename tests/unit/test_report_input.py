"""Вход отчёта за сутки: разделы, сигналы по разделам, итог, заглушки Kp и защита от лишнего."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from heliogeo.contracts.signals import load_signals
from heliogeo.features.compute import FeatureRow, History
from heliogeo.forecast.kp import KpForecast, KpForecastStatus
from heliogeo.functions.stored import day_moment
from heliogeo.mirror.raw import RawStore
from heliogeo.report.input import (
    SECTIONS,
    ReportInputError,
    _plausible,
    build_report_input,
    time_text,
)
from heliogeo.storage.objects import LocalObjectStore
from tests.mocks.swx import FETCHED_AT, QUIET_DATA, STORM_DATA, answered_for, load_fixture

QUIET_DAY = date(2026, 9, 1)
STORM_DAY = date(2024, 5, 11)


def _store(
    tmp_path: Path, fixture: str, day: date, *, drop: tuple[str, ...] = ()
) -> LocalObjectStore:
    store = LocalObjectStore(tmp_path / "raw")
    body = answered_for(load_fixture(fixture), day_moment(day))
    for key in drop:
        body.pop(key, None)
    RawStore(store).save_snapshot(
        endpoint="data",
        body=json.dumps(body).encode(),
        requested_at=day_moment(day),
        window=None,
        no_live=True,
        fetched_at=FETCHED_AT,
    )
    return store


def _payload(tmp_path: Path, fixture: str, day: date, **kwargs: Any) -> dict[str, Any]:
    return build_report_input(day, _store(tmp_path, fixture, day, **kwargs)).to_payload()


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in payload["sections"] if s["name"] == name)


def _ids(payload: dict[str, Any], name: str) -> set[str]:
    return {s["id"] for s in _section(payload, name)["signals"]}


# --- Спокойные сутки ---


def test_quiet_day_has_four_sections_in_order_and_is_issued_after_midnight(tmp_path: Path) -> None:
    payload = _payload(tmp_path, QUIET_DATA, QUIET_DAY)

    assert [s["name"] for s in payload["sections"]] == list(SECTIONS)
    assert payload["metadata"]["issued_utc"] == "2026-09-02T00:30:00+00:00"
    assert payload["metadata"]["valid_until_utc"] == "2026-09-03T00:30:00+00:00"
    assert payload["day"] == "2026-09-01"
    json.dumps(payload)  # только JSON-типы


def test_quiet_day_is_all_clear_with_no_alerts(tmp_path: Path) -> None:
    payload = _payload(tmp_path, QUIET_DATA, QUIET_DAY)

    assert payload["summary"] is not None and payload["summary"]["id"] == "all_clear"
    assert payload["summary"]["unaffected"]  # итог перечисляет, на что обстановка не влияет
    assert "no_earth_directed_cme" in payload["forbidden_claims"]
    assert _section(payload, "Solar Activity")["observed"]["ar_count"] == 6


def test_event_times_are_preformatted_like_noaa_and_values_are_rounded(tmp_path: Path) -> None:
    payload = _payload(tmp_path, QUIET_DATA, QUIET_DAY)
    activity = _section(payload, "Solar Activity")["observed"]

    assert activity["flares_top"] and all("UTC" in f["time_text"] for f in activity["flares_top"])
    assert all(len(f["time_text"]) == len("01/2110 UTC") for f in activity["flares_top"])
    assert len(activity["regions_top"]) <= 5 and len(activity["cmes_top"]) <= 3
    kp_mean = _section(payload, "Geospace")["observed"]["kp_mean_1d"]
    assert kp_mean == round(kp_mean, 3)


def test_cme_list_does_not_carry_the_lost_direction_flag(tmp_path: Path) -> None:
    for fixture, day in ((QUIET_DATA, QUIET_DAY), (STORM_DATA, STORM_DAY)):
        cmes = _section(_payload(tmp_path / str(day), fixture, day), "Solar Activity")["observed"][
            "cmes_top"
        ]
        assert all("earth_directed" not in c for c in cmes)


def test_out_of_range_feature_values_and_rounded_flux_never_reach_the_text(tmp_path: Path) -> None:
    row = FeatureRow(
        t=datetime(2026, 9, 1, tzinfo=UTC),
        feature_set_v="x",
        values={"sw_speed_min": -9999.9, "sw_speed_avg": 380.0, "ar_has_delta": True},
        quality={},
    )

    values = _plausible(row, load_signals())

    assert "sw_speed_min" not in values and values["sw_speed_avg"] == 380.0
    assert values["ar_has_delta"] is True
    flux = _section(_payload(tmp_path, QUIET_DATA, QUIET_DAY), "Solar Activity")["observed"]
    assert "flare_max_flux_24h" not in flux  # 8.6e-6 после округления стало бы «0.0»


# --- Буря ---


def test_storm_day_signals_land_in_their_own_sections(tmp_path: Path) -> None:
    payload = _payload(tmp_path, STORM_DATA, STORM_DAY)

    assert {"geomag_storm_g5", "dst_storm_extreme"} <= _ids(payload, "Geospace")
    assert {"radio_blackout_r3", "cme_earth_directed_seen"} <= _ids(payload, "Solar Activity")
    assert "sw_speed_high" in _ids(payload, "Solar Wind")
    assert "geomag_storm_g5" not in _ids(payload, "Solar Wind")
    assert payload["summary"] is None  # при тревожных сигналах итога «всё в норме» нет


def test_signals_carry_what_the_model_needs_and_nothing_empty(tmp_path: Path) -> None:
    signal = next(
        s for s in _section(_payload(tmp_path, STORM_DATA, STORM_DAY), "Geospace")["signals"]
        if s["id"] == "geomag_storm_g5"
    )  # fmt: skip

    assert signal["severity"] == "alert" and signal["explain"] and signal["title"]
    assert all(value not in (None, [], "") for value in signal.values())


def test_llm_notes_follow_the_values_present_in_the_section(tmp_path: Path) -> None:
    payload = _payload(tmp_path, STORM_DATA, STORM_DAY)

    assert _section(payload, "Solar Wind")["notes"]
    assert _section(payload, "Energetic Particle")["notes"] == []


# --- Нет данных ---


def test_missing_wind_gives_a_missing_data_signal_in_the_wind_section(tmp_path: Path) -> None:
    payload = _payload(tmp_path, QUIET_DATA, QUIET_DAY, drop=("solar_wind",))

    assert "sw_unavailable" in _ids(payload, "Solar Wind")
    assert payload["summary"] is None or payload["summary"]["id"] != "all_clear"


def test_no_stored_day_is_an_error_with_the_reason(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "raw")

    with pytest.raises(ReportInputError, match="2026-09-01"):
        build_report_input(QUIET_DAY, store)


# --- Прогнозы Kp ---


def test_default_forecasters_are_stubs_and_flag_no_usable_forecast(tmp_path: Path) -> None:
    payload = _payload(tmp_path, QUIET_DATA, QUIET_DAY)

    assert [f["status"] for f in payload["kp_forecasts"]] == ["stub"] * 3
    assert payload["kp_forecast_available"] is False


def test_a_real_model_switches_the_availability_flag(tmp_path: Path) -> None:
    class Model:
        name = "kp_real"

        def forecast(self, history: History, issued_utc: datetime) -> KpForecast:
            return KpForecast(
                model=self.name,
                status=KpForecastStatus.OK,
                issued_utc=issued_utc,
                start_utc=datetime(2026, 9, 2, tzinfo=UTC),
                values=(2.0, 3.0),
            )

    store = _store(tmp_path, QUIET_DATA, QUIET_DAY)
    payload = build_report_input(QUIET_DAY, store, forecasters=[Model()]).to_payload()

    assert payload["kp_forecast_available"] is True
    assert payload["kp_forecasts"][0]["max_kp"] == 3.0


def test_time_text_uses_utc() -> None:
    assert time_text(datetime(2026, 9, 1, 21, 10, tzinfo=UTC)) == "01/2110 UTC"
