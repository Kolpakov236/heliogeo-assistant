"""Движок сигналов на небольшом синтетическом контракте: условия, качество, пустота, итоги."""

from datetime import UTC, datetime
from typing import Any

import pytest

from heliogeo.contracts.signals import Condition, Severity, SignalsContract
from heliogeo.features.compute import FeatureRow
from heliogeo.signals import holds, select_signals
from tests.mocks.contracts import ENVELOPE

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _contract() -> SignalsContract:
    """kp и ветер критичны; flux пуст законно, когда kp равен нулю."""
    features = {
        "kp": {
            "type": "number",
            "meaning": "Kp",
            "valid_range": [0, 9],
            "critical": True,
            "on_missing": {"signal": "data_gap"},
        },
        "wind": {
            "type": "number",
            "meaning": "ветер",
            "valid_range": [0, 3000],
            "critical": True,
            "on_missing": {"signal": "data_gap"},
        },
        "flux": {
            "type": "number",
            "meaning": "поток",
            "valid_range": [0, 10],
            "empty_ok_when": {"feature": "kp", "eq": 0},
        },
    }
    signals: list[dict[str, Any]] = [
        {"id": "data_gap", "severity": "notice", "kind": "missing_data", "explain": "нет данных"},
        {
            "id": "storm",
            "severity": "warning",
            "when": {"feature": "kp", "gte": 5},
            "requires_quality": ["ok"],
            "explain": "буря",
        },
        {
            "id": "fast_wind",
            "severity": "notice",
            "when": {
                "all": [{"feature": "wind", "gte": 600}, {"not": {"feature": "kp", "gte": 5}}]
            },
            "explain": "быстрый ветер",
        },
        {
            "id": "all_clear",
            "severity": "info",
            "kind": "summary",
            "when": {"no_signals_at_least": "notice", "critical": "all_present"},
            "explain": "норма",
        },
        {
            "id": "partial",
            "severity": "info",
            "kind": "summary",
            "when": {"no_signals_at_least": "warning", "critical": "some_missing"},
            "explain": "часть данных",
        },
    ]
    return SignalsContract.model_validate({**ENVELOPE, "features": features, "signals": signals})


def _row(quality: str = "ok", **values: float | None) -> FeatureRow:
    names = {"kp": None, "wind": None, "flux": None, **values}
    return FeatureRow(
        t=NOW,
        feature_set_v="test",
        values=names,
        quality={k: (quality if v is not None else "missing") for k, v in names.items()},
    )


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("gt", False), ("gte", True), ("lt", False), ("lte", True), ("eq", True), ("ne", False)],
)
def test_comparison_operators(operator: str, expected: bool) -> None:
    condition = Condition.model_validate({"feature": "kp", operator: 5})
    assert holds(condition, {"kp": 5.0}) is expected


def test_in_and_combinators_and_missing_value() -> None:
    inside = Condition.model_validate({"feature": "z", "in": ["E", "F"]})
    assert holds(inside, {"z": "F"}) and not holds(inside, {"z": "H"})

    pair = [{"feature": "a", "gt": 1}, {"feature": "b", "lt": 1}]
    both = Condition.model_validate({"all": pair})
    either = Condition.model_validate({"any": pair})
    assert holds(both, {"a": 2, "b": 0}) and not holds(both, {"a": 2, "b": 5})
    assert holds(either, {"a": 0, "b": 0}) and not holds(either, {"a": 0, "b": 5})
    # значения нет — условие не выполнено (а не «меньше нуля» или «норма»)
    assert not holds(inside, {"z": None}) and not holds(inside, {})


def test_quiet_row_gives_all_clear_without_signals() -> None:
    selection = select_signals(_contract(), _row(kp=2.0, wind=400.0, flux=1.0))
    assert selection.fired == () and selection.missing_features == ()
    assert selection.summary is not None and selection.summary.id == "all_clear"


def test_alarm_fires_and_blocks_the_summary() -> None:
    selection = select_signals(_contract(), _row(kp=6.0, wind=400.0, flux=1.0))
    assert selection.ids() == {"storm"}
    assert selection.summary is None
    assert [r.id for r in selection.at_least(Severity.WARNING)] == ["storm"]
    assert selection.at_least(Severity.ALERT) == ()


def test_quality_not_allowed_by_the_rule_suppresses_the_signal() -> None:
    degraded_storm = select_signals(_contract(), _row(quality="degraded", kp=6.0, wind=1.0))
    assert degraded_storm.ids() == set()  # storm требует качества ok
    degraded_wind = select_signals(_contract(), _row(quality="degraded", wind=700.0, kp=1.0))
    assert degraded_wind.ids() == {"fast_wind"}  # у этого правила качество не ограничено


def test_feature_absent_from_quality_map_does_not_crash_or_fire() -> None:
    row = FeatureRow(t=NOW, feature_set_v="t", values={"kp": 6.0, "wind": 1.0}, quality={})
    assert select_signals(_contract(), row).ids() == set()


def test_missing_critical_gives_one_gap_signal_and_partial_summary() -> None:
    selection = select_signals(_contract(), _row(kp=2.0))  # ветра нет
    assert selection.ids() == {"data_gap"}
    assert selection.missing_features == ("wind",)
    assert selection.summary is not None and selection.summary.id == "partial"


def test_two_missing_features_share_one_gap_signal_and_no_data_has_no_summary() -> None:
    selection = select_signals(_contract(), _row())
    assert [r.id for r in selection.fired] == ["data_gap"]  # kp и wind делят один сигнал
    assert selection.missing_features == ("kp", "wind")
    assert selection.summary is None


def test_legitimately_empty_feature_is_not_a_gap() -> None:
    selection = select_signals(_contract(), _row(kp=0.0, wind=400.0))  # flux пуст, kp = 0
    assert selection.missing_features == ()
    assert selection.summary is not None and selection.summary.id == "all_clear"
