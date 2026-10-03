"""Сериализация фактов: цикл без потерь, детерминированность, строгий разбор."""

import copy
import json
import pickle
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import pytest

from heliogeo.facts import DisplayPolicy, Fact, FactKind, QCStatus, Uncertainty
from tests.mocks.facts import T_FROM, T_TO, make_fact, make_missing_fact, make_quiet_day_facts

TOP_LEVEL_KEYS = [
    "id",
    "kind",
    "value",
    "unit",
    "t_valid_from",
    "t_valid_to",
    "provenance",
    "uncertainty",
    "qc",
    "display",
    "must_mention",
]

FULL_DISPLAY = DisplayPolicy(
    unit_text={"ru": "км/с", "en": "km/s"},
    value_map={"ru": {"G1": "слабая"}, "en": {"G1": "minor"}},
    statement={"ru": "Скорость {value} {unit}.", "en": "Speed {value} {unit}."},
    round_to=25,
)

FACTS = [
    make_fact(),
    make_fact(kind=FactKind.INTERVAL, value=(347.9, 449.6)),
    make_fact(kind=FactKind.CATEGORICAL, value="G1", display=FULL_DISPLAY),
    make_fact(kind=FactKind.BOOLEAN, value=True),
    make_fact(kind=FactKind.COUNT, value=6),
    make_fact(kind=FactKind.COUNT, value=10**12),
    make_fact(kind=FactKind.LIST, value=["4513", "4520"]),
    make_fact(kind=FactKind.LIST, value=["C5.7", 1, 2.5, True]),
    make_fact(kind=FactKind.TIMESTAMP, value=datetime(2026, 9, 1, 12, 38, 5, 123456, tzinfo=UTC)),
    make_fact(kind=FactKind.TIME_RANGE, value=(T_FROM, T_TO)),
    make_fact(kind=FactKind.DISTRIBUTION, value={"quiet": 0.4, "unsettled": 0.35, "G1": 0.25}),
    make_fact(value=0.1 + 0.2),  # 0.30000000000000004 не должно округлиться
    make_fact(value=-0.0),
    make_missing_fact(),
    make_fact(qc=QCStatus.DEGRADED, value=None),
    make_fact(qc=QCStatus.STALE),
    make_fact(qc=QCStatus.INVALID, value=1.0e6),
    make_fact(
        must_mention=True,
        uncertainty=Uncertainty(confidence=0.7, sigma=12.5, log_sigma=0.3, note="оценка"),
        display=FULL_DISPLAY,
    ),
    *make_quiet_day_facts().values(),
]
FACT_IDS = [f"{index}-{fact.id}-{fact.kind.value}" for index, fact in enumerate(FACTS)]


# --- Цикл без потерь ---


@pytest.mark.parametrize("fact", FACTS, ids=FACT_IDS)
def test_dict_roundtrip_is_lossless(fact: Fact) -> None:
    assert Fact.from_dict(fact.to_dict()) == fact


@pytest.mark.parametrize("fact", FACTS, ids=FACT_IDS)
def test_json_roundtrip_is_lossless(fact: Fact) -> None:
    text = json.dumps(fact.to_dict(), ensure_ascii=False, allow_nan=False)

    restored = Fact.from_dict(json.loads(text))

    assert restored == fact
    assert hash(restored) == hash(fact)
    assert restored.to_dict() == fact.to_dict()


@pytest.mark.parametrize("fact", FACTS, ids=FACT_IDS)
def test_pickle_and_copy_preserve_the_fact(fact: Fact) -> None:
    assert pickle.loads(pickle.dumps(fact)) == fact
    assert copy.deepcopy(fact) == fact
    assert copy.copy(fact) == fact


def test_float_precision_is_preserved() -> None:
    fact = make_fact(value=0.1 + 0.2)

    restored = Fact.from_dict(json.loads(json.dumps(fact.to_dict())))

    assert restored.value == 0.30000000000000004


# --- Форма словаря ---


def test_top_level_keys_have_a_fixed_order() -> None:
    assert list(make_fact().to_dict()) == TOP_LEVEL_KEYS


def test_serialization_is_deterministic() -> None:
    """Порядок ключей в исходных словарях не влияет на результат."""
    first = make_fact(
        display=DisplayPolicy(
            unit_text={"ru": "км/с", "en": "km/s"}, value_map={"ru": {"b": "Б", "a": "А"}}
        )
    )
    second = make_fact(
        display=DisplayPolicy(
            unit_text={"en": "km/s", "ru": "км/с"}, value_map={"ru": {"a": "А", "b": "Б"}}
        )
    )

    assert json.dumps(first.to_dict()) == json.dumps(second.to_dict())


def test_encodings_by_kind() -> None:
    assert make_fact().to_dict()["value"] == 449.6
    assert make_fact(kind=FactKind.INTERVAL, value=(1, 2)).to_dict()["value"] == [1.0, 2.0]
    assert make_fact(kind=FactKind.LIST, value=["a", 1]).to_dict()["value"] == ["a", 1]
    assert make_fact(kind=FactKind.TIMESTAMP, value=T_TO).to_dict()["value"] == (
        "2026-09-01T12:00:00+00:00"
    )
    assert make_fact(kind=FactKind.TIME_RANGE, value=(T_FROM, T_TO)).to_dict()["value"] == [
        "2026-09-01T00:00:00+00:00",
        "2026-09-01T12:00:00+00:00",
    ]
    distribution = make_fact(kind=FactKind.DISTRIBUTION, value={"quiet": 0.6, "G1": 0.4})
    assert distribution.to_dict()["value"] == [["quiet", 0.6], ["G1", 0.4]]  # порядок сохранён
    assert make_missing_fact().to_dict()["value"] is None


def test_enums_are_serialized_as_plain_strings() -> None:
    data = make_fact().to_dict()

    assert data["kind"] == "scalar"
    assert not isinstance(data["kind"], Enum)
    assert data["qc"] == "ok"
    assert not isinstance(data["qc"], Enum)


def test_times_are_serialized_in_utc_with_offset() -> None:
    data = make_fact().to_dict()

    assert data["t_valid_from"] == "2026-09-01T00:00:00+00:00"
    assert data["t_valid_to"] == "2026-09-01T12:00:00+00:00"
    assert data["provenance"]["fetched"] == "2026-09-01T12:00:05+00:00"


def test_to_dict_returns_independent_containers() -> None:
    fact = make_fact()

    data = fact.to_dict()
    data["display"]["unit_text"]["ru"] = "изменено"
    data["provenance"]["files"].append("x")

    assert fact.display.unit_text["ru"] == "км/с"
    assert len(fact.provenance.files) == 1


# --- Разбор: допустимые отклонения ---


def test_from_dict_applies_defaults_for_optional_keys() -> None:
    data = make_fact().to_dict()
    for key in ("unit", "uncertainty", "display", "must_mention"):
        del data[key]

    fact = Fact.from_dict(data)

    assert fact.unit is None
    assert fact.uncertainty == Uncertainty()
    assert fact.display == DisplayPolicy()
    assert fact.must_mention is False


def test_from_dict_accepts_z_suffix_and_offsets() -> None:
    data = make_fact().to_dict()
    data["t_valid_from"] = "2026-09-01T00:00:00Z"
    data["t_valid_to"] = "2026-09-01T15:00:00+03:00"

    fact = Fact.from_dict(data)

    assert fact.t_valid_from == T_FROM
    assert fact.t_valid_to == T_TO
    assert fact.t_valid_to.tzinfo is UTC


def test_from_dict_accepts_time_without_seconds_like_the_source() -> None:
    """Источник отдаёт время выбросов как ``2026-09-01T21:24Z``."""
    data = make_fact(kind=FactKind.TIMESTAMP, value=T_TO).to_dict()
    data["value"] = "2026-09-01T21:24Z"

    assert Fact.from_dict(data).value == datetime(2026, 9, 1, 21, 24, tzinfo=UTC)


def test_from_dict_accepts_distribution_as_object() -> None:
    data = make_fact(kind=FactKind.DISTRIBUTION, value={"quiet": 0.6, "G1": 0.4}).to_dict()
    data["value"] = {"quiet": 0.6, "G1": 0.4}

    assert Fact.from_dict(data).value == (("quiet", 0.6), ("G1", 0.4))


# --- Разбор: ошибки ---


def _broken(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(make_fact().to_dict())
    data.update(changes)
    return data


@pytest.mark.parametrize("key", ["id", "kind", "value", "t_valid_from", "t_valid_to", "qc"])
def test_from_dict_requires_mandatory_keys(key: str) -> None:
    data = make_fact().to_dict()
    del data[key]

    with pytest.raises(ValueError, match=f"нет обязательных полей: {key}"):
        Fact.from_dict(data)


def test_from_dict_requires_provenance() -> None:
    data = make_fact().to_dict()
    del data["provenance"]

    with pytest.raises(ValueError, match="нет обязательных полей: provenance"):
        Fact.from_dict(data)


def test_from_dict_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Факт: неизвестные поля: extra"):
        Fact.from_dict(_broken(extra=1))


@pytest.mark.parametrize("data", [[], "fact", None, 5])
def test_from_dict_rejects_non_mappings(data: Any) -> None:
    with pytest.raises(ValueError, match="Факт: нужен словарь"):
        Fact.from_dict(data)


def test_from_dict_rejects_unknown_kind_and_status() -> None:
    with pytest.raises(ValueError, match="kind: неизвестное значение 'vector'"):
        Fact.from_dict(_broken(kind="vector"))
    with pytest.raises(ValueError, match="qc: неизвестное значение 'bad'"):
        Fact.from_dict(_broken(qc="bad"))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2026-09-01T12:00:00", "нужен datetime с часовым поясом"),  # наивное время
        ("yesterday", "не время ISO 8601"),
        (5, "нужна строка ISO 8601"),
        (None, "нужна строка ISO 8601"),
    ],
)
def test_from_dict_rejects_bad_times(value: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Fact.from_dict(_broken(t_valid_from=value))


@pytest.mark.parametrize(
    ("section", "extra_key"),
    [("provenance", "x"), ("uncertainty", "x"), ("display", "x")],
)
def test_from_dict_rejects_unknown_nested_keys(section: str, extra_key: str) -> None:
    data = _broken()
    data[section][extra_key] = 1

    with pytest.raises(ValueError, match=f"{section}: неизвестные поля: {extra_key}"):
        Fact.from_dict(data)


def test_from_dict_requires_provenance_source_and_method() -> None:
    data = _broken()
    del data["provenance"]["source"]

    with pytest.raises(ValueError, match="provenance: нет обязательных полей: source"):
        Fact.from_dict(data)


@pytest.mark.parametrize("section", ["provenance", "uncertainty", "display"])
def test_from_dict_rejects_non_mapping_sections(section: str) -> None:
    with pytest.raises(ValueError, match=f"{section}: нужен словарь"):
        Fact.from_dict(_broken(**{section: ["x"]}))


def test_from_dict_rejects_files_given_as_a_string() -> None:
    data = _broken()
    data["provenance"]["files"] = "a.txt"

    with pytest.raises(ValueError, match="provenance.files"):
        Fact.from_dict(data)


@pytest.mark.parametrize(
    ("kind", "value", "message"),
    [
        (FactKind.SCALAR, "fast", "нужно конечное число"),
        (FactKind.COUNT, -1, "для count"),
        (FactKind.TIMESTAMP, 5, "нужна строка ISO 8601"),
        (FactKind.TIMESTAMP, "2026-09-01T12:00:00", "часовым поясом"),
        (FactKind.TIME_RANGE, ["2026-09-01T12:00:00Z"], "нужна пара"),
        (FactKind.TIME_RANGE, ["2026-09-01T12:00:00Z", "2026-09-01T00:00:00Z"], "позже конца"),
        (FactKind.DISTRIBUTION, [["quiet", 0.5]], "сумма вероятностей"),
        (FactKind.INTERVAL, [2, 1], "начало интервала больше конца"),
    ],
)
def test_from_dict_validates_value_for_kind(kind: FactKind, value: Any, message: str) -> None:
    data = make_fact(kind=kind, value=_valid_value(kind), display=DisplayPolicy()).to_dict()
    data["value"] = value

    with pytest.raises(ValueError, match=re.escape(message)):
        Fact.from_dict(data)


def test_from_dict_rechecks_status_invariants() -> None:
    with pytest.raises(ValueError, match="статус missing означает отсутствие значения"):
        Fact.from_dict(_broken(qc="missing"))
    with pytest.raises(ValueError, match="статус ok требует значения"):
        Fact.from_dict(_broken(value=None))


def test_from_dict_rejects_non_bool_must_mention() -> None:
    with pytest.raises(ValueError, match="must_mention: нужен bool"):
        Fact.from_dict(_broken(must_mention="yes"))


def _valid_value(kind: FactKind) -> Any:
    return {
        FactKind.SCALAR: 1.0,
        FactKind.COUNT: 1,
        FactKind.TIMESTAMP: T_TO,
        FactKind.TIME_RANGE: (T_FROM, T_TO),
        FactKind.DISTRIBUTION: {"quiet": 1.0},
        FactKind.INTERVAL: (1.0, 2.0),
    }[kind]
