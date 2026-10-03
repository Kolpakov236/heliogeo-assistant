"""Модель факта: создание, инварианты, статусы, значения по видам, неизменяемость."""

import dataclasses
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from heliogeo.facts import DisplayPolicy, Fact, FactKind, Provenance, QCStatus, Uncertainty
from tests.mocks.facts import T_FROM, T_TO, make_fact, make_missing_fact, make_provenance

MSK = timezone(timedelta(hours=3))

# --- Перечисления ---


def test_fact_kinds() -> None:
    assert {kind.value for kind in FactKind} == {
        "scalar",
        "interval",
        "categorical",
        "boolean",
        "count",
        "list",
        "timestamp",
        "time_range",
        "distribution",
    }


def test_qc_statuses() -> None:
    assert {status.value for status in QCStatus} == {
        "ok",
        "degraded",
        "stale",
        "missing",
        "invalid",
    }


def test_enums_compare_equal_to_their_strings() -> None:
    assert FactKind.SCALAR == "scalar"
    assert QCStatus.MISSING == "missing"


# --- Создание, значения по умолчанию, неизменяемость ---


def test_minimal_fact_gets_defaults() -> None:
    fact = Fact(
        id="a.b",
        kind=FactKind.COUNT,
        value=3,
        t_valid_from=T_FROM,
        t_valid_to=T_TO,
        provenance=make_provenance(),
        qc=QCStatus.OK,
    )

    assert fact.unit is None
    assert fact.uncertainty == Uncertainty()
    assert fact.display == DisplayPolicy()
    assert fact.must_mention is False


def test_fields_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        Fact("a.b", FactKind.COUNT, 3)  # type: ignore[misc]


def test_fact_is_immutable() -> None:
    fact = make_fact()

    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.value = 1.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.provenance.source = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.display.round_to = 1.0  # type: ignore[misc]


def test_equal_facts_are_equal_and_hashable() -> None:
    first, second = make_fact(), make_fact()

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert {first: "x"}[second] == "x"


def test_different_content_gives_different_facts() -> None:
    assert make_fact() != make_fact(value=450.0)
    assert make_fact() != make_fact(qc=QCStatus.DEGRADED)
    assert make_fact() != make_fact(must_mention=True)


def test_scalar_int_becomes_float() -> None:
    value = make_fact(value=5).value

    assert value == 5.0
    assert isinstance(value, float)


def test_fact_errors_name_the_fact() -> None:
    with pytest.raises(ValueError, match="Факт 'sw.speed.max_24h'"):
        make_fact(value="fast")


def test_fact_can_be_extended_by_a_subclass() -> None:
    """Прогнозные факты добавят свои обязательные поля, не ломая базовые."""

    @dataclasses.dataclass(frozen=True, kw_only=True)
    class ExtendedFact(Fact):
        target_from: datetime

    fact = ExtendedFact(
        id="forecast.storm.level",
        kind=FactKind.CATEGORICAL,
        value="G1",
        t_valid_from=T_FROM,
        t_valid_to=T_TO,
        provenance=make_provenance(),
        qc=QCStatus.OK,
        target_from=T_TO,
    )

    assert fact.target_from == T_TO
    assert fact.with_value("G2").target_from == T_TO
    assert isinstance(fact.with_value("G2"), ExtendedFact)


# --- Идентификатор ---


@pytest.mark.parametrize(
    "fact_id",
    [
        "sw.speed.max_24h",
        "geo.kp.max_24h",
        "ar.count",
        "forecast.flare_m.prob",
        "part.proton10.peak",
        "a.b",
    ],
)
def test_valid_ids(fact_id: str) -> None:
    assert make_fact(id=fact_id).id == fact_id


@pytest.mark.parametrize(
    "fact_id",
    [
        "speed",  # один сегмент
        "Sw.speed",
        "sw.Speed",
        "sw.",
        ".sw",
        "sw..speed",
        "sw.1speed",  # сегмент начинается с цифры
        "sw speed",
        "",
        "sw.speed-max",
        "сw.speed",  # первая буква кириллическая
        "sw.speed\n",
        " sw.speed",
    ],
)
def test_invalid_ids_rejected(fact_id: str) -> None:
    with pytest.raises(ValueError, match="идентификатор"):
        make_fact(id=fact_id)


@pytest.mark.parametrize("fact_id", [5, None, b"sw.speed"])
def test_non_string_id_rejected(fact_id: Any) -> None:
    with pytest.raises(ValueError, match="идентификатор"):
        make_fact(id=fact_id)


# --- Окно валидности ---


@pytest.mark.parametrize("field", ["t_valid_from", "t_valid_to"])
def test_naive_time_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=f"{field}: нужен datetime с часовым поясом"):
        make_fact(**{field: datetime(2026, 9, 1, 6, 0)})


@pytest.mark.parametrize("field", ["t_valid_from", "t_valid_to"])
def test_non_datetime_time_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=f"{field}: нужен datetime"):
        make_fact(**{field: "2026-09-01T06:00:00+00:00"})


def test_times_are_normalized_to_utc() -> None:
    fact = make_fact(
        t_valid_from=datetime(2026, 9, 1, 3, 0, tzinfo=MSK),  # 00:00 UTC
        t_valid_to=datetime(2026, 9, 1, 15, 0, tzinfo=MSK),  # 12:00 UTC
    )

    assert fact.t_valid_from == T_FROM
    assert fact.t_valid_to == T_TO
    assert fact.t_valid_from.tzinfo is UTC
    assert fact.t_valid_to.tzinfo is UTC


def test_valid_from_after_valid_to_rejected() -> None:
    with pytest.raises(ValueError, match="t_valid_from позже t_valid_to"):
        make_fact(t_valid_from=T_TO, t_valid_to=T_FROM)


def test_zero_length_window_allowed() -> None:
    fact = make_fact(t_valid_from=T_TO, t_valid_to=T_TO)

    assert fact.t_valid_from == fact.t_valid_to


# --- Значения по видам ---

VALID_VALUES = [
    pytest.param(FactKind.SCALAR, 449.6, 449.6, id="scalar"),
    pytest.param(FactKind.SCALAR, -15, -15.0, id="scalar-negative-int"),
    pytest.param(FactKind.INTERVAL, [347.9, 449.6], (347.9, 449.6), id="interval-list"),
    pytest.param(FactKind.INTERVAL, (3, 3), (3.0, 3.0), id="interval-degenerate"),
    pytest.param(FactKind.CATEGORICAL, "G1", "G1", id="categorical"),
    pytest.param(FactKind.BOOLEAN, True, True, id="boolean-true"),
    pytest.param(FactKind.BOOLEAN, False, False, id="boolean-false"),
    pytest.param(FactKind.COUNT, 0, 0, id="count-zero"),
    pytest.param(FactKind.COUNT, 6, 6, id="count"),
    pytest.param(FactKind.LIST, ["4513", "4520"], ("4513", "4520"), id="list-str"),
    pytest.param(FactKind.LIST, [4513, 4520], (4513, 4520), id="list-int"),
    pytest.param(FactKind.LIST, ["C5.7", 1, 2.5, True], ("C5.7", 1, 2.5, True), id="list-mixed"),
    pytest.param(FactKind.LIST, [], (), id="list-empty"),
    pytest.param(
        FactKind.TIMESTAMP,
        datetime(2026, 9, 1, 15, 38, tzinfo=MSK),
        datetime(2026, 9, 1, 12, 38, tzinfo=UTC),
        id="timestamp-to-utc",
    ),
    pytest.param(FactKind.TIME_RANGE, [T_FROM, T_TO], (T_FROM, T_TO), id="time-range"),
    pytest.param(
        FactKind.DISTRIBUTION,
        {"quiet": 0.5, "G1": 0.3, "G2": 0.2},
        (("quiet", 0.5), ("G1", 0.3), ("G2", 0.2)),
        id="distribution-mapping",
    ),
    pytest.param(
        FactKind.DISTRIBUTION,
        [("quiet", 0.5), ("G1", 0.5)],
        (("quiet", 0.5), ("G1", 0.5)),
        id="distribution-pairs",
    ),
    pytest.param(
        FactKind.DISTRIBUTION,
        {"a": 0.333, "b": 0.333, "c": 0.333},  # округление до трёх знаков допустимо
        (("a", 0.333), ("b", 0.333), ("c", 0.333)),
        id="distribution-rounded-sum",
    ),
]


@pytest.mark.parametrize(("kind", "raw", "expected"), VALID_VALUES)
def test_value_is_normalized_for_kind(kind: FactKind, raw: Any, expected: Any) -> None:
    fact = make_fact(kind=kind, value=raw, display=DisplayPolicy())

    assert fact.value == expected
    assert type(fact.value) is type(expected)


INVALID_VALUES = [
    pytest.param(FactKind.SCALAR, True, "нужно конечное число", id="scalar-bool"),
    pytest.param(FactKind.SCALAR, "449", "нужно конечное число", id="scalar-str"),
    pytest.param(FactKind.SCALAR, float("nan"), "нужно конечное число", id="scalar-nan"),
    pytest.param(FactKind.SCALAR, float("inf"), "нужно конечное число", id="scalar-inf"),
    pytest.param(FactKind.SCALAR, [1.0], "нужно конечное число", id="scalar-list"),
    pytest.param(FactKind.COUNT, -1, "для count", id="count-negative"),
    pytest.param(FactKind.COUNT, 1.5, "для count", id="count-fraction"),
    pytest.param(FactKind.COUNT, 3.0, "для count", id="count-float"),
    pytest.param(FactKind.COUNT, True, "для count", id="count-bool"),
    pytest.param(FactKind.COUNT, "3", "для count", id="count-str"),
    pytest.param(FactKind.BOOLEAN, 1, "для boolean", id="boolean-int"),
    pytest.param(FactKind.BOOLEAN, "true", "для boolean", id="boolean-str"),
    pytest.param(FactKind.CATEGORICAL, "", "нужна непустая строка", id="categorical-empty"),
    pytest.param(FactKind.CATEGORICAL, "  ", "нужна непустая строка", id="categorical-blank"),
    pytest.param(FactKind.CATEGORICAL, 5, "нужна непустая строка", id="categorical-int"),
    pytest.param(FactKind.INTERVAL, [2, 1], "начало интервала больше конца", id="interval-order"),
    pytest.param(FactKind.INTERVAL, [1], "нужна пара", id="interval-one"),
    pytest.param(FactKind.INTERVAL, [1, 2, 3], "нужна пара", id="interval-three"),
    pytest.param(FactKind.INTERVAL, "ab", "нужна пара", id="interval-str"),
    pytest.param(FactKind.INTERVAL, [1, "x"], "нужно конечное число", id="interval-str-item"),
    pytest.param(FactKind.INTERVAL, [1, float("nan")], "нужно конечное число", id="interval-nan"),
    pytest.param(FactKind.LIST, "abc", "для list нужна последовательность", id="list-str"),
    pytest.param(FactKind.LIST, 5, "для list нужна последовательность", id="list-int"),
    pytest.param(FactKind.LIST, [[1]], "элемент списка", id="list-nested"),
    pytest.param(FactKind.LIST, [None], "элемент списка", id="list-none"),
    pytest.param(FactKind.LIST, [float("nan")], "элемент списка", id="list-nan"),
    pytest.param(FactKind.LIST, [""], "нужна непустая строка", id="list-empty-str"),
    pytest.param(FactKind.TIMESTAMP, datetime(2026, 9, 1, 12), "часовым поясом", id="ts-naive"),
    pytest.param(FactKind.TIMESTAMP, "2026-09-01T12:00:00Z", "нужен datetime", id="ts-str"),
    pytest.param(FactKind.TIME_RANGE, [T_TO, T_FROM], "позже конца", id="range-order"),
    pytest.param(FactKind.TIME_RANGE, [T_FROM], "нужна пара", id="range-one"),
    pytest.param(
        FactKind.TIME_RANGE, [datetime(2026, 9, 1), T_TO], "часовым поясом", id="range-naive"
    ),
    pytest.param(FactKind.DISTRIBUTION, {}, "не может быть пустым", id="distribution-empty"),
    pytest.param(FactKind.DISTRIBUTION, {"a": 0.5}, "сумма вероятностей", id="distribution-sum"),
    pytest.param(FactKind.DISTRIBUTION, {"a": 1.2, "b": -0.2}, "вне [0, 1]", id="distribution-p"),
    pytest.param(
        FactKind.DISTRIBUTION,
        [("a", 0.5), ("a", 0.5)],
        "не должны повторяться",
        id="distribution-duplicate",
    ),
    pytest.param(FactKind.DISTRIBUTION, "abc", "нужен словарь", id="distribution-str"),
    pytest.param(
        FactKind.DISTRIBUTION, {"": 1.0}, "нужна непустая строка", id="distribution-label"
    ),
    pytest.param(
        FactKind.DISTRIBUTION, {"a": float("nan")}, "нужно конечное число", id="distribution-nan"
    ),
]


@pytest.mark.parametrize(("kind", "raw", "message"), INVALID_VALUES)
def test_wrong_value_shape_rejected(kind: FactKind, raw: Any, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        make_fact(kind=kind, value=raw, display=DisplayPolicy())


# --- Статусы качества ---


def test_missing_status_forbids_a_value() -> None:
    with pytest.raises(ValueError, match="статус missing означает отсутствие значения"):
        make_fact(qc=QCStatus.MISSING)


def test_ok_status_requires_a_value() -> None:
    with pytest.raises(ValueError, match="статус ok требует значения"):
        make_fact(value=None)


@pytest.mark.parametrize(
    ("qc", "value"),
    [
        (QCStatus.OK, 1.0),
        (QCStatus.DEGRADED, 1.0),
        (
            QCStatus.DEGRADED,
            None,
        ),  # направление выброса неизвестно: значения нет, но не «нет данных»
        (QCStatus.STALE, 1.0),
        (QCStatus.STALE, None),
        (QCStatus.INVALID, 9999.0),  # значение вне диапазона остаётся для разбора
        (QCStatus.INVALID, None),
        (QCStatus.MISSING, None),
    ],
)
def test_allowed_status_value_combinations(qc: QCStatus, value: float | None) -> None:
    fact = make_fact(qc=qc, value=value)

    assert fact.qc is qc
    assert fact.value == value


def test_status_and_kind_accept_strings() -> None:
    fact = make_fact(qc="degraded", kind="scalar")

    assert fact.qc is QCStatus.DEGRADED
    assert fact.kind is FactKind.SCALAR


def test_unknown_status_rejected() -> None:
    with pytest.raises(ValueError, match="qc: неизвестное значение 'bad'"):
        make_fact(qc="bad")


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="kind: неизвестное значение 'vector'"):
        make_fact(kind="vector")


@pytest.mark.parametrize(
    ("qc", "has_value", "expected"),
    [
        (QCStatus.OK, True, True),
        (QCStatus.DEGRADED, True, True),
        (QCStatus.DEGRADED, False, False),
        (QCStatus.STALE, True, False),
        (QCStatus.INVALID, True, False),
        (QCStatus.MISSING, False, False),
    ],
)
def test_is_usable(qc: QCStatus, has_value: bool, expected: bool) -> None:
    fact = make_fact(qc=qc, value=449.6 if has_value else None)

    assert fact.is_usable() is expected


def test_missing_fact_mock_is_not_usable() -> None:
    fact = make_missing_fact()

    assert fact.qc is QCStatus.MISSING
    assert fact.value is None
    assert not fact.is_usable()


# --- with_value ---


def test_with_value_returns_a_changed_copy() -> None:
    fact = make_fact()

    changed = fact.with_value(500.0)

    assert changed.value == 500.0
    assert fact.value == 449.6  # исходный не изменился
    assert changed is not fact


def test_with_value_keeps_everything_else() -> None:
    fact = make_fact(must_mention=True, uncertainty=Uncertainty(confidence=0.8))

    changed = fact.with_value(500.0)

    assert dataclasses.replace(changed, value=fact.value) == fact


def test_with_value_revalidates_the_shape() -> None:
    with pytest.raises(ValueError, match="нужно конечное число"):
        make_fact().with_value("fast")


def test_with_value_none_needs_a_status_change() -> None:
    fact = make_fact()

    with pytest.raises(ValueError, match="статус ok требует значения"):
        fact.with_value(None)

    missing = fact.with_value(None, qc=QCStatus.MISSING)
    assert missing.value is None
    assert missing.qc is QCStatus.MISSING


def test_with_value_can_change_only_the_status() -> None:
    degraded = make_fact().with_value(449.6, qc=QCStatus.DEGRADED)

    assert degraded.value == 449.6
    assert degraded.qc is QCStatus.DEGRADED


# --- Остальные поля факта ---


def test_must_mention_must_be_bool() -> None:
    with pytest.raises(ValueError, match="must_mention: нужен bool"):
        make_fact(must_mention=1)


@pytest.mark.parametrize(
    ("field", "expected"),
    [("provenance", "Provenance"), ("uncertainty", "Uncertainty"), ("display", "DisplayPolicy")],
)
def test_nested_objects_must_have_the_right_type(field: str, expected: str) -> None:
    with pytest.raises(ValueError, match=f"{field}: нужен {expected}"):
        make_fact(**{field: {"source": "x"}})


def test_unit_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="unit: нужна непустая строка"):
        make_fact(unit="")


# --- Provenance ---


def test_provenance_defaults() -> None:
    provenance = Provenance(source="swx", method="direct")

    assert provenance.files == ()
    assert provenance.fetched is None
    assert provenance.fingerprint is None
    assert provenance.note is None


@pytest.mark.parametrize("field", ["source", "method"])
def test_provenance_requires_source_and_method(field: str) -> None:
    params = {"source": "swx", "method": "direct", field: " "}

    with pytest.raises(ValueError, match=f"provenance.{field}: нужна непустая строка"):
        Provenance(**params)


def test_provenance_files_are_normalized_to_tuple() -> None:
    provenance = Provenance(source="swx", method="direct", files=["a.txt", "b.txt"])  # type: ignore[arg-type]

    assert provenance.files == ("a.txt", "b.txt")
    assert hash(provenance) == hash(
        Provenance(source="swx", method="direct", files=("a.txt", "b.txt"))
    )


def test_provenance_files_must_not_be_a_string() -> None:
    with pytest.raises(ValueError, match="provenance.files"):
        Provenance(source="swx", method="direct", files="a.txt")  # type: ignore[arg-type]


def test_provenance_files_must_not_contain_blanks() -> None:
    with pytest.raises(ValueError, match=re.escape("provenance.files[1]")):
        Provenance(source="swx", method="direct", files=("a.txt", ""))


def test_provenance_fetched_is_normalized_to_utc() -> None:
    provenance = Provenance(
        source="swx", method="direct", fetched=datetime(2026, 9, 1, 15, 0, tzinfo=MSK)
    )

    assert provenance.fetched == T_TO
    assert provenance.fetched is not None
    assert provenance.fetched.tzinfo is UTC


def test_provenance_fetched_must_be_aware() -> None:
    with pytest.raises(ValueError, match="provenance.fetched"):
        Provenance(source="swx", method="direct", fetched=datetime(2026, 9, 1, 12, 0))


@pytest.mark.parametrize("field", ["fingerprint", "note"])
def test_provenance_optional_texts_must_not_be_blank(field: str) -> None:
    with pytest.raises(ValueError, match=f"provenance.{field}"):
        Provenance(source="swx", method="direct", **{field: ""})


# --- Uncertainty ---


def test_uncertainty_defaults() -> None:
    uncertainty = Uncertainty()

    assert uncertainty.confidence == 1.0
    assert uncertainty.sigma is None
    assert uncertainty.log_sigma is None
    assert uncertainty.note is None


@pytest.mark.parametrize("confidence", [0.0, 0.3, 1.0, 1])
def test_uncertainty_accepts_confidence_in_unit_interval(confidence: float) -> None:
    assert Uncertainty(confidence=confidence).confidence == float(confidence)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), True, "0.5"])
def test_uncertainty_rejects_bad_confidence(confidence: Any) -> None:
    with pytest.raises(ValueError, match="uncertainty.confidence"):
        Uncertainty(confidence=confidence)


@pytest.mark.parametrize("field", ["sigma", "log_sigma"])
def test_uncertainty_errors_are_non_negative_numbers(field: str) -> None:
    assert getattr(Uncertainty(**{field: 2}), field) == 2.0
    assert getattr(Uncertainty(**{field: 0}), field) == 0.0
    with pytest.raises(ValueError, match=f"uncertainty.{field}"):
        Uncertainty(**{field: -1})
    with pytest.raises(ValueError, match=f"uncertainty.{field}"):
        Uncertainty(**{field: float("inf")})


def test_uncertainty_note_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="uncertainty.note"):
        Uncertainty(note=" ")


# --- DisplayPolicy ---


def test_display_policy_defaults() -> None:
    policy = DisplayPolicy()

    assert policy.round_to is None
    assert policy.formatter is None
    assert dict(policy.unit_text) == {}
    assert dict(policy.value_map) == {}
    assert dict(policy.statement) == {}


@pytest.mark.parametrize("round_to", [0, -1, float("nan"), True, "25"])
def test_round_to_must_be_a_positive_number(round_to: Any) -> None:
    with pytest.raises(ValueError, match="display.round_to"):
        DisplayPolicy(round_to=round_to)


def test_round_to_is_stored_as_float() -> None:
    assert DisplayPolicy(round_to=25).round_to == 25.0


def test_unknown_formatter_rejected() -> None:
    with pytest.raises(ValueError, match="display.formatter: неизвестный форматтер 'roman'"):
        DisplayPolicy(formatter="roman")


def test_round_to_and_formatter_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="взаимно исключают"):
        DisplayPolicy(round_to=1, formatter="kp")


@pytest.mark.parametrize("field", ["unit_text", "statement"])
def test_language_maps_reject_unknown_language_and_blank_text(field: str) -> None:
    template = "{value}"
    with pytest.raises(ValueError, match="неизвестный язык 'de'"):
        DisplayPolicy(**{field: {"de": template}})
    with pytest.raises(ValueError, match=re.escape(f"display.{field}[ru]")):
        DisplayPolicy(**{field: {"ru": " "}})
    with pytest.raises(ValueError, match=f"display.{field}"):
        DisplayPolicy(**{field: ["ru"]})


@pytest.mark.parametrize(
    "value_map",
    [
        {"de": {"G1": "x"}},  # неизвестный язык
        {"ru": {}},  # пустая карта
        {"ru": ["G1"]},  # не словарь
        {"ru": {"G1": ""}},  # пустое слово
        {"ru": {"": "слабая"}},  # пустой ключ
        ["ru"],  # не словарь
    ],
)
def test_value_map_shape_validated(value_map: Any) -> None:
    with pytest.raises(ValueError, match="display.value_map"):
        DisplayPolicy(value_map=value_map)


def test_statement_template_needs_the_value_field() -> None:
    with pytest.raises(ValueError, match=re.escape("в шаблоне нет поля {value}")):
        DisplayPolicy(statement={"ru": "Скорость ветра за период."})


@pytest.mark.parametrize(
    "template",
    ["{count}", "{value:.1f}", "{value!r}", "{0}", "{}", "{value.real}", "{value} {other}"],
)
def test_statement_template_allows_only_value_and_unit(template: str) -> None:
    with pytest.raises(ValueError, match="допустимы только поля"):
        DisplayPolicy(statement={"ru": template}, unit_text={"ru": "км/с"})


def test_broken_statement_template_rejected() -> None:
    with pytest.raises(ValueError, match="некорректный шаблон"):
        DisplayPolicy(statement={"ru": "Скорость {value"})


def test_statement_with_unit_needs_unit_text_for_the_same_language() -> None:
    with pytest.raises(ValueError, match=re.escape("display.unit_text[ru] не задан")):
        DisplayPolicy(
            statement={"ru": "Скорость {value} {unit}."},
            unit_text={"en": "km/s"},
        )


def test_statement_literal_braces_are_allowed() -> None:
    policy = DisplayPolicy(statement={"en": "Raw {{value}} equals {value}"})

    assert policy.statement["en"] == "Raw {{value}} equals {value}"


def test_display_policy_mappings_are_read_only() -> None:
    policy = DisplayPolicy(
        unit_text={"ru": "км/с"},
        value_map={"ru": {"G1": "слабая"}},
        statement={"ru": "{value}"},
    )

    with pytest.raises(TypeError):
        policy.unit_text["en"] = "km/s"  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.value_map["en"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.value_map["ru"]["G2"] = "умеренная"  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.statement["en"] = "{value}"  # type: ignore[index]


def test_display_policy_copies_input_mappings() -> None:
    unit_text = {"ru": "км/с"}
    words = {"G1": "слабая"}
    policy = DisplayPolicy(unit_text=unit_text, value_map={"ru": words})

    unit_text["ru"] = "изменено"
    words["G1"] = "изменено"

    assert policy.unit_text["ru"] == "км/с"
    assert policy.value_map["ru"]["G1"] == "слабая"


def test_display_policy_equality_ignores_key_order() -> None:
    first = DisplayPolicy(unit_text={"ru": "км/с", "en": "km/s"})
    second = DisplayPolicy(unit_text={"en": "km/s", "ru": "км/с"})

    assert first == second
    assert hash(first) == hash(second)
    assert first != DisplayPolicy(unit_text={"ru": "км/с"})
