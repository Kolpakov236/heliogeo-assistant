"""Правила сигналов: схема, сверка с признаками, норма и пустые значения на реальных данных.

Движка сигналов в ``src`` нет (он следующий этап), поэтому небольшой вычислитель условий живёт
здесь, в тесте: он показывает, что правила выбирают нужные сигналы на спокойном дне, на бури и
при пустых данных.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from heliogeo.contracts.features import FeaturesContract, load_feature_specs
from heliogeo.contracts.loader import contracts_root
from heliogeo.contracts.signals import (
    Condition,
    Severity,
    SignalKind,
    SignalRule,
    SignalsContract,
    SummaryWhen,
    check_signals,
    load_signals,
)
from heliogeo.contracts.validate import validate_directory
from heliogeo.features.compute import FeatureRow, compute_features
from heliogeo.features.daily import extract_observation_daily
from tests.mocks.contracts import (
    ENVELOPE,
    make_contracts_tree,
    make_feature,
    make_signals_body,
    write_contract,
)
from tests.mocks.swx import QUIET_DATA, STORM_DATA, load_fixture

SPECS = load_feature_specs()
CONTRACT = load_signals()


# --- Вычислитель условий (только для тестов) ---


def _leaf_holds(leaf: Condition, values: Mapping[str, Any]) -> bool:
    value = values.get(leaf.feature)
    if value is None:
        return False
    operator, argument = leaf.operator()
    return {
        "gt": lambda: value > argument,
        "gte": lambda: value >= argument,
        "lt": lambda: value < argument,
        "lte": lambda: value <= argument,
        "eq": lambda: value == argument,
        "ne": lambda: value != argument,
        "in": lambda: value in argument,
    }[operator]()


def holds(condition: Condition, values: Mapping[str, Any]) -> bool:
    if condition.feature is not None:
        return _leaf_holds(condition, values)
    if condition.all_ is not None:
        return all(holds(child, values) for child in condition.all_)
    if condition.any_ is not None:
        return any(holds(child, values) for child in condition.any_)
    assert condition.not_ is not None
    return not holds(condition.not_, values)


def select(
    contract: SignalsContract, row: FeatureRow
) -> tuple[list[SignalRule], SignalRule | None]:
    """Сработавшие сигналы и итоговый сигнал для строки признаков."""
    values, quality = row.values, row.quality
    fired: list[SignalRule] = []
    for rule in contract.signals:
        if rule.kind is not SignalKind.SIGNAL or not isinstance(rule.when, Condition):
            continue
        used = rule.when.features()
        if any(values.get(f) is None or quality[f] not in rule.requires_quality for f in used):
            continue
        if holds(rule.when, values):
            fired.append(rule)

    by_id = {rule.id: rule for rule in contract.signals}
    for name, profile in contract.features.items():
        if profile.on_missing is None or values.get(name) is not None:
            continue
        rule = by_id[profile.on_missing.signal]
        if rule not in fired:
            fired.append(rule)

    present = [values.get(name) is not None for name in contract.critical_features()]
    for rule in contract.signals:
        mode = rule.summary_mode()
        if mode is None or not isinstance(rule.when, SummaryWhen):
            continue
        if any(f.severity.at_least(rule.when.no_signals_at_least) for f in fired):
            continue
        if mode == "all_present" and all(present):
            return fired, rule
        if mode == "some_missing" and any(present) and not all(present):
            return fired, rule
    return fired, None


def ids(rules: list[SignalRule]) -> set[str]:
    return {rule.id for rule in rules}


# --- Данные для сценариев ---


def _row(fixture: str, day: date, t: datetime, *, previous: date) -> FeatureRow:
    """Строка признаков по реальному снимку; previous нужен признакам с задержкой в сутки (КВМ)."""
    data = extract_observation_daily(load_fixture(fixture), d=day, raw_s3_key="k")
    before = extract_observation_daily(load_fixture(fixture), d=previous, raw_s3_key="k")
    return compute_features(t, {day: data, previous: before}, SPECS)


QUIET = _row(
    QUIET_DATA, date(2026, 9, 1), datetime(2026, 9, 2, 3, 0, tzinfo=UTC), previous=date(2026, 8, 31)
)
STORM = _row(
    STORM_DATA,
    date(2024, 5, 11),
    datetime(2024, 5, 12, 3, 0, tzinfo=UTC),
    previous=date(2024, 5, 10),
)


def _without(row: FeatureRow, *names: str) -> FeatureRow:
    values = {k: (None if k in names else v) for k, v in row.values.items()}
    quality = {k: ("missing" if k in names else v) for k, v in row.quality.items()}
    return FeatureRow(t=row.t, feature_set_v=row.feature_set_v, values=values, quality=quality)


def _with(row: FeatureRow, **changes: Any) -> FeatureRow:
    return FeatureRow(row.t, row.feature_set_v, {**row.values, **changes}, row.quality)


# --- Реальный контракт ---


def test_repo_contract_loads_and_matches_feature_specs() -> None:
    assert set(CONTRACT.features) == set(SPECS.by_id())
    assert len(CONTRACT.features) == 40
    assert check_signals(CONTRACT, SPECS) == []


def test_every_critical_feature_has_a_missing_data_signal() -> None:
    by_id = {rule.id: rule for rule in CONTRACT.signals}
    critical = CONTRACT.critical_features()
    assert critical
    for name in critical:
        on_missing = CONTRACT.features[name].on_missing
        assert on_missing is not None
        assert by_id[on_missing.signal].kind is SignalKind.MISSING_DATA


def test_repo_contract_has_all_clear_and_partial_data_summaries() -> None:
    modes = sorted(rule.summary_mode() or "" for rule in CONTRACT.signals if rule.summary_mode())
    assert modes == ["all_present", "some_missing"]


def test_validate_directory_accepts_repo_contracts() -> None:
    report = validate_directory(contracts_root(), check_lock=True)
    assert report.ok, [f.errors for f in report.files if f.errors] + report.problems


# --- Сценарии на реальных данных ---


def test_quiet_day_gives_all_clear_and_nothing_alarming() -> None:
    fired, summary = select(CONTRACT, QUIET)
    assert summary is not None and summary.id == "all_clear"
    assert not [r for r in fired if r.severity.at_least(Severity.NOTICE)]
    # направление одного выброса не определено: итог «всё в норме» не должен его скрывать
    assert "cme_direction_undetermined" in ids(fired)
    assert any("no_earth_directed_cme" in r.forbids_claims for r in fired)


def test_all_clear_names_unaffected_systems() -> None:
    _, summary = select(CONTRACT, QUIET)
    assert summary is not None
    assert {s.value for s in summary.unaffected} == {
        "hf_com",
        "gnss",
        "satcom",
        "radiation",
        "power_grid",
    }


def test_storm_day_fires_alerts_and_is_not_all_clear() -> None:
    fired, summary = select(CONTRACT, STORM)
    assert summary is None
    assert {
        "geomag_storm_g5",
        "dst_storm_extreme",
        "radio_blackout_r3",
        "cme_earth_directed_seen",
        "cme_earth_directed_fast",
        "ar_delta_present",
        "sw_speed_high",
        "sw_bz_south_strong",
    } <= ids(fired)


@pytest.mark.parametrize(
    ("kp", "expected"),
    [
        (4.3, "geomag_active"),
        (5.3, "geomag_storm_g1"),
        (6.0, "geomag_storm_g2"),
        (7.0, "geomag_storm_g3"),
    ],
)
def test_exactly_one_geomagnetic_level_fires(kp: float, expected: str) -> None:
    fired = ids(select(CONTRACT, _with(QUIET, kp_max_1d=kp))[0])
    levels = {"geomag_active", "geomag_storm_g1", "geomag_storm_g2", "geomag_storm_g3"}
    assert fired & levels == {expected}


def test_flare_class_a_is_normal_not_a_signal() -> None:
    fired, summary = select(CONTRACT, _with(QUIET, flare_max_flux_24h=5.0e-8))  # класс A
    assert not [i for i in ids(fired) if i.startswith("radio_blackout")]
    assert summary is not None and summary.id == "all_clear"


def test_empty_kp_is_reported_and_is_not_all_clear() -> None:
    fired, summary = select(CONTRACT, _without(QUIET, "kp_max_1d"))
    assert "kp_unavailable" in ids(fired)
    assert summary is not None and summary.id == "partial_data"


def test_missing_cme_forbids_the_no_cme_claim() -> None:
    fired, _ = select(CONTRACT, _without(QUIET, "cme_count_24h", "cme_earth_directed"))
    (rule,) = [r for r in fired if r.id == "cme_unavailable"]
    assert "no_earth_directed_cme" in rule.forbids_claims


def test_no_data_at_all_gives_missing_signals_but_no_calm_summary() -> None:
    row = compute_features(datetime(2026, 9, 2, 3, 0, tzinfo=UTC), {}, SPECS)
    fired, summary = select(CONTRACT, row)
    assert summary is None
    assert {"kp_unavailable", "sw_unavailable", "ar_unavailable", "flares_unavailable"} <= ids(
        fired
    )


def test_empty_max_speed_with_zero_cmes_is_not_a_gap() -> None:
    profile = CONTRACT.features["cme_speed_max_24h"]
    assert profile.empty_ok_when is not None
    assert holds(profile.empty_ok_when, {"cme_count_24h": 0, "cme_speed_max_24h": None})
    # счётчика нет — пустота уже не «нет событий», а «нет данных»
    assert not holds(profile.empty_ok_when, {"cme_count_24h": None})


def test_degraded_quality_still_allows_solar_wind_rules() -> None:
    assert QUIET.quality["sw_speed_max"] == "degraded"  # OMNI: не L1
    quality = {**STORM.quality, "sw_speed_max": "degraded"}
    row = FeatureRow(STORM.t, STORM.feature_set_v, dict(STORM.values), quality)
    assert "sw_speed_high" in ids(select(CONTRACT, row)[0])


# --- Схема: ошибки ---


def _parse(**overrides: Any) -> SignalsContract:
    return SignalsContract.model_validate({**ENVELOPE, **make_signals_body(**overrides)})


def _signals(*extra: dict[str, Any]) -> list[dict[str, Any]]:
    return [*make_signals_body()["signals"], *extra]


def _profile(**fields: Any) -> dict[str, Any]:
    return {
        "kp_max_1d": {"type": "number", "meaning": "Максимум Kp", "valid_range": [0, 9], **fields}
    }


def _rule(**fields: Any) -> dict[str, Any]:
    return {"id": "r", "severity": "info", "explain": "x", **fields}


def test_minimal_contract_is_valid() -> None:
    assert _parse().features["kp_max_1d"].type.value == "number"


_ALL_CLEAR_AGAIN = {
    "id": "all_clear",
    "severity": "info",
    "kind": "summary",
    "when": {"no_signals_at_least": "notice", "critical": "all_present"},
    "explain": "дубль",
}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"signals": _signals(_ALL_CLEAR_AGAIN)}, "повторяющиеся id"),
        (
            {"signals": [_rule(when={"feature": "kp_max_1d", "gte": 5})]},
            "итоговый сигнал «всё в норме»",
        ),
        ({"features": _profile(critical=True)}, "обязателен on_missing"),
        (
            {"features": _profile(critical=True, on_missing={"signal": "nope"})},
            "нет в signals",
        ),
        (
            {"features": _profile(critical=True, on_missing={"signal": "all_clear"})},
            "kind: missing_data",
        ),
        ({"features": _profile(valid_range=[5, 5])}, "левая граница"),
        ({"features": _profile(allowed_values=["a"])}, "только у строковых"),
        (
            {
                "features": _profile(
                    on_missing={"signal": "all_clear"},
                    empty_ok_when={"feature": "kp_max_1d", "eq": 0},
                )
            },
            "взаимоисключающие",
        ),
    ],
    ids=[
        "duplicate-signal-id",
        "no-all-clear",
        "critical-without-on-missing",
        "on-missing-unknown-signal",
        "on-missing-wrong-kind",
        "range-not-increasing",
        "allowed-values-on-number",
        "on-missing-and-empty-ok",
    ],
)
def test_contract_rejects_inconsistent_bodies(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _parse(**overrides)


def _band(label: str, lt: float | None = None) -> dict[str, Any]:
    band: dict[str, Any] = {"label": label, "explain": "x"}
    if lt is not None:
        band["lt"] = lt
    return band


@pytest.mark.parametrize(
    ("bands", "message"),
    [
        ([_band("a", 3), _band("b", 3), _band("c")], "строго возрастать"),
        ([_band("a", 3), _band("b", 5)], "у последней полосы нет lt"),
        ([_band("a"), _band("b")], "кроме последней"),
        ([_band("a", 12), _band("b")], "строго возрастать"),
        ([_band("a", 3), _band("a")], "метки полос"),
    ],
    ids=["equal-edges", "last-has-lt", "middle-without-lt", "edge-out-of-range", "duplicate-label"],
)
def test_bands_cannot_have_gaps_or_overlaps(bands: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _parse(features=_profile(bands=bands))


def test_bands_need_a_numeric_feature_with_range() -> None:
    profile = {"s": {"type": "string", "meaning": "x", "bands": [_band("a")]}}
    with pytest.raises(ValidationError, match="числового признака"):
        _parse(features=profile)


@pytest.mark.parametrize(
    "when",
    [
        {"feature": "kp_max_1d"},
        {"feature": "kp_max_1d", "gte": 1, "lt": 5},
        {"feature": "kp_max_1d", "gte": "1e-5"},
        {"feature": "kp_max_1d", "gte": True},
        {"feature": "kp_max_1d", "between": [1, 5]},
        {"all": []},
        {"all": [{"feature": "kp_max_1d", "gte": 1}], "any": [{"feature": "kp_max_1d", "lt": 9}]},
        {"gte": 1},
        {"feature": "kp_max_1d", "in": []},
    ],
    ids=[
        "no-operator",
        "two-operators",
        "string-threshold",
        "bool-threshold",
        "unknown-operator",
        "empty-all",
        "all-and-any",
        "no-feature",
        "empty-in",
    ],
)
def test_condition_rejects_bad_forms(when: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(when)


def test_condition_supports_combinators_and_collects_features() -> None:
    condition = Condition.model_validate(
        {
            "all": [
                {"feature": "kp_max_1d", "gte": 5},
                {"not": {"feature": "ar_has_delta", "eq": True}},
                {"any": [{"feature": "sw_sector", "in": ["toward"]}, {"feature": "doy", "lt": 10}]},
            ]
        }
    )
    assert condition.features() == {"kp_max_1d", "ar_has_delta", "sw_sector", "doy"}
    assert holds(condition, {"kp_max_1d": 6, "ar_has_delta": False, "sw_sector": "toward"})
    assert not holds(condition, {"kp_max_1d": 6, "ar_has_delta": True, "sw_sector": "toward"})


def test_yaml_scientific_notation_without_sign_is_rejected_with_a_hint() -> None:
    """«1e-5» PyYAML читает как строку: ошибка подсказывает, как писать степень."""
    with pytest.raises(ValidationError, match=r"1\.0e-5"):
        Condition.model_validate(yaml.safe_load("feature: kp_max_1d\ngte: 1e-5\n"))


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (_rule(), "требует условия"),
        (_rule(kind="summary"), "итоговый сигнал требует"),
        (
            _rule(kind="missing_data", when={"feature": "kp_max_1d", "gte": 1}),
            "when не имеет",
        ),
        (
            _rule(requires_quality=[], when={"feature": "kp_max_1d", "gte": 1}),
            "не может быть пустым",
        ),
        (_rule(unaffected=["gnss"], when={"feature": "kp_max_1d", "gte": 1}), "только у итоговых"),
        (_rule(id="A-b", kind="missing_data"), "pattern"),
        (_rule(severity="fatal", kind="missing_data"), "severity"),
    ],
    ids=[
        "signal-without-when",
        "summary-without-when",
        "missing-data-with-when",
        "empty-quality",
        "unaffected-on-signal",
        "bad-id",
        "bad-severity",
    ],
)
def test_signal_rule_rejects_bad_forms(rule: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SignalRule.model_validate(rule)


def test_contract_models_are_immutable() -> None:
    with pytest.raises(ValidationError):
        CONTRACT.signals[0].id = "other"  # type: ignore[misc]


# --- Сверка с features/specs.yaml ---


def _features(*names: str) -> FeaturesContract:
    return FeaturesContract.model_validate(
        {**ENVELOPE, "features": [make_feature(id=name) for name in names]}
    )


def _with_rule(rule: dict[str, Any], profile: dict[str, Any] | None = None) -> SignalsContract:
    return _parse(features=profile or _profile(), signals=_signals(rule))


def test_check_reports_missing_and_unknown_profiles() -> None:
    problems = check_signals(_parse(), _features("kp_max_1d", "dst_min_1d"))
    assert any("нет паспорта признака 'dst_min_1d'" in p for p in problems)
    problems = check_signals(_parse(), _features("dst_min_1d"))
    assert any("паспорт признака 'kp_max_1d', которого нет" in p for p in problems)


def test_check_reports_condition_on_feature_without_profile() -> None:
    rule = _rule(when={"feature": "ghost", "gte": 1})
    problems = check_signals(_with_rule(rule), _features("kp_max_1d"))
    assert any("у признака 'ghost' нет паспорта" in p for p in problems)


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ({"feature": "kp_max_1d", "gte": 12}, "вне допустимого диапазона"),
        ({"feature": "kp_max_1d", "eq": "high"}, "нужен числовой порог"),
    ],
    ids=["out-of-range", "wrong-type"],
)
def test_check_validates_threshold_against_the_profile(when: dict[str, Any], message: str) -> None:
    problems = check_signals(_with_rule(_rule(when=when)), _features("kp_max_1d"))
    assert any(message in p for p in problems)


def test_check_validates_string_boolean_and_integer_features() -> None:
    profile = {
        "z": {"type": "string", "meaning": "x", "allowed_values": ["A", "B"]},
        "d": {"type": "boolean", "meaning": "x"},
        "n": {"type": "integer", "meaning": "x", "valid_range": [0, 10]},
    }
    rules = [
        _rule(id="r1", when={"feature": "z", "in": ["A", "Q"]}),
        _rule(id="r2", when={"feature": "d", "eq": 1}),
        _rule(id="r3", when={"feature": "n", "eq": 2.5}),
        _rule(id="r4", when={"feature": "z", "gte": 1}),
    ]
    contract = _parse(features=profile, signals=_signals(*rules))
    problems = "\n".join(check_signals(contract, _features("z", "d", "n")))
    assert "вне allowed_values" in problems
    assert "нужен true/false" in problems
    assert "должен быть целым" in problems
    assert "только для числовых" in problems


def test_validate_directory_reports_cross_contract_problems(tmp_path: Path) -> None:
    tree = make_contracts_tree(tmp_path / "contracts")
    rule = _rule(when={"feature": "kp_max_1d", "gte": 99})
    write_contract(
        tree, "rules/signals.yaml", {**ENVELOPE, **make_signals_body(signals=_signals(rule))}
    )
    report = validate_directory(tree, check_lock=False)
    assert any("вне допустимого диапазона" in problem for problem in report.problems)


def test_validate_directory_accepts_minimal_signals_tree(tmp_path: Path) -> None:
    tree = make_contracts_tree(tmp_path / "contracts")
    assert validate_directory(tree, check_lock=False).problems == []
