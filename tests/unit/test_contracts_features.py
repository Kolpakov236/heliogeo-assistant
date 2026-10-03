"""Тесты модели спецификации признаков (B3-01) на синтетических данных."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from heliogeo.contracts.features import (
    FeatureGroup,
    FeaturesContract,
    FeatureSpec,
    FeatureStatus,
    load_feature_specs,
    parse_delay,
)
from heliogeo.contracts.loader import ContractError, validate_model


def _feature(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "kp_max_1d",
        "group": "activity_history",
        "description": "Максимум Kp за 24 часа",
        "available_delay": "PT3H",
    }
    data.update(overrides)
    return data


def _contract(*features: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": "0.1.0",
        "approved_by": None,
        "approved_at": None,
        "features": list(features) or [_feature()],
    }
    data.update(overrides)
    return data


def _problems(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ContractError) as exc_info:
        validate_model(data, FeaturesContract, "specs.yaml")
    return exc_info.value.problems


# --- parse_delay ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PT3H", timedelta(hours=3)),
        ("P1D", timedelta(days=1)),
        ("PT0S", timedelta(0)),
        ("PT90M", timedelta(minutes=90)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("P1DT12H", timedelta(days=1, hours=12)),
        ("P27DT6H36M", timedelta(days=27, hours=6, minutes=36)),
        ("PT45S", timedelta(seconds=45)),
        ("P0D", timedelta(0)),
    ],
)
def test_parse_delay_accepts_iso_durations(text: str, expected: timedelta) -> None:
    assert parse_delay(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "P",
        "PT",
        "P1DT",
        "3h",
        "1D",
        "pt3h",
        "PT3H ",
        "PT1H1D",
        "PT1.5H",
        "-PT1H",
        "P1W",
        "P1M",
        "P1Y",
        "PT٣H",  # арабо-индийская цифра: для \d это цифра, для ISO 8601 — нет
    ],
)
def test_parse_delay_rejects_everything_but_days_and_time(text: str) -> None:
    """Недели, месяцы и годы не допускаются (их длина неоднозначна), как и неполные записи."""
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_delay(text)


@pytest.mark.parametrize("value", [3, 0, 1.5, None, ["PT3H"]])
def test_parse_delay_requires_a_string(value: object) -> None:
    """Число без единицы измерения (available_delay: 3) — ошибка, а не «3 секунды»."""
    with pytest.raises(ValueError, match="строкой ISO 8601"):
        parse_delay(value)


def test_parse_delay_passes_timedelta_through_but_not_negative_ones() -> None:
    assert parse_delay(timedelta(hours=2)) == timedelta(hours=2)
    with pytest.raises(ValueError, match="отрицательной"):
        parse_delay(timedelta(hours=-1))


# --- FeatureSpec ---


def test_feature_spec_defaults() -> None:
    spec = FeatureSpec.model_validate(_feature())
    assert spec.available_delay == timedelta(hours=3)
    assert spec.group is FeatureGroup.ACTIVITY_HISTORY
    assert spec.status is FeatureStatus.PLANNED
    assert spec.note is None


def test_feature_spec_text_fields_are_stripped() -> None:
    spec = FeatureSpec.model_validate(_feature(description="  Максимум Kp  ", note=" заметка "))
    assert (spec.description, spec.note) == ("Максимум Kp", "заметка")


def test_feature_spec_is_immutable() -> None:
    spec = FeatureSpec.model_validate(_feature())
    with pytest.raises(ValidationError):
        spec.status = FeatureStatus.ACTIVE  # type: ignore[misc]


def test_available_delay_is_mandatory() -> None:
    """Критерий B3-01: у каждой записи есть задержка — без неё запись не принимается."""
    data = _feature()
    del data["available_delay"]
    with pytest.raises(ValidationError) as exc_info:
        FeatureSpec.model_validate(data)
    assert [(e["loc"], e["type"]) for e in exc_info.value.errors()] == [
        (("available_delay",), "missing")
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "KP"},
        {"id": "1kp"},
        {"id": "kp-max"},
        {"id": "kp max"},
        {"id": ""},
        {"id": "kp_max_1d "},
        {"group": "weather"},
        {"description": ""},
        {"description": "   "},
        {"note": ""},
        {"status": "done"},
        {"available_delay": "3h"},
        {"available_delay": 3},
        {"delay": "PT3H"},  # лишнее поле
    ],
    ids=[
        "id-upper",
        "id-digit-first",
        "id-dash",
        "id-space",
        "id-empty",
        "id-trailing-space",
        "group-unknown",
        "description-empty",
        "description-blank",
        "note-empty",
        "status-unknown",
        "delay-bad-format",
        "delay-number",
        "extra-field",
    ],
)
def test_feature_spec_rejects_invalid_records(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FeatureSpec.model_validate(_feature(**overrides))


# --- FeaturesContract ---


def test_contract_keeps_feature_order_and_helpers() -> None:
    contract = FeaturesContract.model_validate(
        _contract(
            _feature(id="a_feature", status="active"),
            _feature(id="b_feature"),
            _feature(id="c_feature", status="active"),
        )
    )
    assert [f.id for f in contract.features] == ["a_feature", "b_feature", "c_feature"]
    assert [f.id for f in contract.active()] == ["a_feature", "c_feature"]
    assert set(contract.by_id()) == {"a_feature", "b_feature", "c_feature"}
    assert contract.by_id()["b_feature"].status is FeatureStatus.PLANNED


def test_contract_without_active_features_has_empty_active_set() -> None:
    assert FeaturesContract.model_validate(_contract()).active() == ()


def test_empty_feature_list_is_rejected() -> None:
    (problem,) = _problems(_contract(features=[]))
    assert problem.startswith("features: ")


def test_duplicate_ids_are_reported_once_with_location() -> None:
    data = _contract(_feature(id="x"), _feature(id="y"), _feature(id="x"), _feature(id="x"))
    assert _problems(data) == ["features: повторяющиеся id признаков: x"]


def test_errors_point_to_the_exact_record() -> None:
    data = _contract(_feature(), _feature(id="second", available_delay="3h"))
    (problem,) = _problems(data)
    assert problem.startswith(
        "features[1].available_delay: задержка должна быть длительностью ISO 8601"
    )


def test_unknown_top_level_key_is_rejected() -> None:
    assert _problems(_contract(extra_key=1)) == ["extra_key: неизвестное поле"]


def test_envelope_is_checked_too() -> None:
    problems = _problems(_contract(version="1.0"))
    assert any(p.startswith("version: версия должна иметь вид X.Y.Z") for p in problems)


# --- загрузка файла ---


def _dump(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "features" / "specs.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_load_feature_specs_reads_file_from_given_root(tmp_path: Path) -> None:
    _dump(tmp_path, _contract(_feature(available_delay="P1D")))
    specs = load_feature_specs(tmp_path)
    assert specs.version == "0.1.0"
    assert specs.features[0].available_delay == timedelta(days=1)


def test_load_feature_specs_reports_invalid_file(tmp_path: Path) -> None:
    _dump(tmp_path, _contract(_feature(available_delay="never")))
    with pytest.raises(ContractError, match=r"features\[0\]\.available_delay"):
        load_feature_specs(tmp_path)


def test_load_feature_specs_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="файл не найден"):
        load_feature_specs(tmp_path)
