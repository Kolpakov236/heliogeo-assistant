"""Проверка мок-фабрик фактов: данные согласованы и совпадают с реальным снимком источника."""

import json
from pathlib import Path
from typing import Any

import pytest

from heliogeo.facts import DisplayPolicy, FactKind, QCStatus
from tests.mocks.facts import (
    FETCHED,
    T_FROM,
    T_TO,
    make_fact,
    make_missing_fact,
    make_provenance,
    make_quiet_day_facts,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FLARE_ORDER = "ABCMX"


def _snapshot() -> dict[str, Any]:
    return json.loads((FIXTURES / "swx_snapshot_2026-09-01.json").read_text(encoding="utf-8"))


def _max_flare(flares: list[dict[str, Any]]) -> str:
    return max((f["class"] for f in flares), key=lambda c: (FLARE_ORDER.index(c[0]), float(c[1:])))


# --- make_fact / make_provenance / make_missing_fact ---


def test_make_fact_default() -> None:
    fact = make_fact()

    assert fact.id == "sw.speed.max_24h"
    assert fact.kind is FactKind.SCALAR
    assert fact.value == 449.6
    assert fact.qc is QCStatus.OK
    assert fact.t_valid_from == T_FROM
    assert fact.t_valid_to == T_TO
    assert fact.provenance.fetched == FETCHED
    assert fact.is_usable()


def test_make_fact_overrides_are_validated() -> None:
    assert make_fact(value=500.0).value == 500.0
    with pytest.raises(ValueError, match="нужно конечное число"):
        make_fact(value="fast")


def test_make_provenance_overrides() -> None:
    provenance = make_provenance(source="x:y", method="count", files=(), fingerprint=None)

    assert provenance.source == "x:y"
    assert provenance.method == "count"
    assert provenance.files == ()
    assert provenance.fingerprint is None


def test_make_missing_fact() -> None:
    fact = make_missing_fact()

    assert fact.id == "part.proton10.peak"
    assert fact.qc is QCStatus.MISSING
    assert fact.value is None
    assert not fact.is_usable()
    assert fact.display == DisplayPolicy()


def test_make_missing_fact_overrides() -> None:
    assert make_missing_fact(id="part.electron.peak").id == "part.electron.peak"


# --- Набор спокойного дня ---


def test_quiet_day_set_is_consistent() -> None:
    facts = make_quiet_day_facts()

    assert len(facts) == 11
    assert all(key == fact.id for key, fact in facts.items())
    missing = [fact.id for fact in facts.values() if fact.qc is QCStatus.MISSING]
    degraded = [fact.id for fact in facts.values() if fact.qc is QCStatus.DEGRADED]
    assert missing == ["part.proton10.peak"]
    assert degraded == ["cme.earth_directed_count"]
    assert sum(fact.is_usable() for fact in facts.values()) == 10


def test_quiet_day_set_is_built_afresh_each_time() -> None:
    assert make_quiet_day_facts() == make_quiet_day_facts()
    assert make_quiet_day_facts() is not make_quiet_day_facts()


def test_quiet_day_set_matches_the_real_snapshot() -> None:
    """Значения набора — не выдуманные: считаются из того же снимка, что и тесты экстрактора."""
    facts = make_quiet_day_facts()
    data = _snapshot()
    wind, geomag, particles = data["solar_wind"], data["geomag"], data["particles"]

    assert facts["sw.speed.max_24h"].value == wind["speed"]["max"]
    assert facts["sw.imf.bt"].value == wind["imf"]["bt"]
    assert facts["geo.kp.max_24h"].value == geomag["kp_obs"]["max"]
    assert facts["geo.dst.min_24h"].value == geomag["dst_min_day"]
    assert facts["flare.count_24h"].value == len(data["flares"])
    assert facts["flare.max_class_24h"].value == _max_flare(data["flares"])
    assert facts["ar.count"].value == len(data["active_regions"])
    assert facts["sep.event"].value == particles["sep"]["gt10_event"]
    assert particles["proton_gt10mev"]["peak"] is None  # поэтому факт part.proton10.peak — missing

    earth_directed = [cme["earth_directed"] for cme in data["cmes"]]
    assert facts["cme.earth_directed_count"].value == earth_directed.count(True)
    assert None in earth_directed  # поэтому счётчик выбросов — degraded, а не ok


def test_quiet_day_set_displays_as_in_the_reference_examples() -> None:
    facts = make_quiet_day_facts()

    assert facts["sw.speed.max_24h"].display_value() == "450"
    assert facts["sw.imf.bt"].display_value() == "5"
    assert facts["geo.kp.max_24h"].display_value() == "2+"


def test_degraded_count_carries_its_reason() -> None:
    fact = make_quiet_day_facts()["cme.earth_directed_count"]

    assert fact.uncertainty.confidence == 0.7
    assert fact.uncertainty.note == "1 из 3 выбросов с неопределённым направлением"
