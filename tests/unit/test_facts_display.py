"""Отображение факта: display_value и canonical_statement."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from heliogeo.facts import DEFAULT_LANG, DisplayPolicy, FactKind, QCStatus
from tests.mocks.facts import T_FROM, T_TO, make_fact, make_missing_fact, make_quiet_day_facts

QUIET = make_quiet_day_facts()
MSK = timezone(timedelta(hours=3))


def _fact(kind: FactKind, value: Any, **display: Any) -> Any:
    return make_fact(kind=kind, value=value, unit=None, display=DisplayPolicy(**display))


# --- display_value: примеры из набора спокойного дня ---


@pytest.mark.parametrize(
    ("fact_id", "expected_ru", "expected_en"),
    [
        ("sw.speed.max_24h", "450", "450"),  # 449.6 до 25 км/с
        ("sw.imf.bt", "5", "5"),  # 5.17 до 1 нТл
        ("geo.kp.max_24h", "2+", "2+"),  # 2.333 в трёхбалльной записи
        ("geo.dst.min_24h", "-15", "-15"),
        ("flare.count_24h", "8", "8"),
        ("flare.max_class_24h", "C5.7", "C5.7"),
        ("ar.count", "6", "6"),
        ("cme.earth_directed_count", "0", "0"),
        ("forecast.storm.level", "слабая", "minor"),
        ("sep.event", "нет", "no"),
    ],
)
def test_quiet_day_display_values(fact_id: str, expected_ru: str, expected_en: str) -> None:
    fact = QUIET[fact_id]

    assert fact.display_value("ru") == expected_ru
    assert fact.display_value("en") == expected_en


def test_default_language_is_russian() -> None:
    fact = make_fact(value=4.084, display=DisplayPolicy(round_to=0.1))

    assert DEFAULT_LANG == "ru"
    assert fact.display_value() == "4,1"
    assert fact.display_value("en") == "4.1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(347.9, "350"), (449.6, "450"), (462.5, "475"), (955.0, "950"), (0.0, "0")],
)
def test_speed_is_rounded_to_25(value: float, expected: str) -> None:
    assert make_fact(value=value).display_value() == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(-9, "-10"), (-15, "-15"), (-406, "-405"), (-167, "-165"), (-2, "0")]
)
def test_dst_is_rounded_to_5(value: float, expected: str) -> None:
    assert QUIET["geo.dst.min_24h"].with_value(value).display_value() == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0.667, "1-"), (1.333, "1+"), (4.667, "5-"), (9.0, "9"), (0.0, "0")]
)
def test_kp_uses_three_step_notation(value: float, expected: str) -> None:
    assert QUIET["geo.kp.max_24h"].with_value(value).display_value() == expected


def test_kp_outside_the_scale_is_an_error_not_a_number() -> None:
    with pytest.raises(ValueError, match="Kp вне шкалы"):
        QUIET["geo.kp.max_24h"].with_value(9.7).display_value()


# --- display_value: ошибки ---


def test_scalar_without_policy_is_an_error() -> None:
    with pytest.raises(ValueError, match="не задана политика отображения числа"):
        make_fact(display=DisplayPolicy()).display_value()


def test_missing_fact_has_no_display_value() -> None:
    with pytest.raises(ValueError, match="не имеет значения"):
        make_missing_fact().display_value()


def test_degraded_fact_without_value_has_no_display_value() -> None:
    fact = make_fact(qc=QCStatus.DEGRADED, value=None)

    with pytest.raises(ValueError, match="не имеет значения"):
        fact.display_value()


def test_unknown_language_is_rejected() -> None:
    with pytest.raises(ValueError, match="Неизвестный язык 'de'"):
        make_fact().display_value("de")  # type: ignore[arg-type]


# --- display_value: виды значений ---


def test_count_has_no_separators() -> None:
    assert _fact(FactKind.COUNT, 1000).display_value() == "1000"


def test_boolean_default_words() -> None:
    fact = _fact(FactKind.BOOLEAN, True)

    assert fact.display_value("ru") == "да"
    assert fact.display_value("en") == "yes"
    assert fact.with_value(False).display_value("ru") == "нет"
    assert fact.with_value(False).display_value("en") == "no"


def test_boolean_value_map_overrides_default_words() -> None:
    fact = _fact(FactKind.BOOLEAN, True, value_map={"ru": {"true": "были", "false": "не было"}})

    assert fact.display_value("ru") == "были"
    assert fact.with_value(False).display_value("ru") == "не было"
    assert fact.display_value("en") == "yes"  # для английского карты нет: слова по умолчанию


def test_boolean_value_map_must_cover_both_values() -> None:
    fact = _fact(FactKind.BOOLEAN, False, value_map={"ru": {"true": "были"}})

    with pytest.raises(ValueError, match="нет в display.value_map"):
        fact.display_value("ru")


def test_categorical_without_map_is_shown_as_is() -> None:
    assert _fact(FactKind.CATEGORICAL, "Dai").display_value("ru") == "Dai"


def test_categorical_uses_value_map_for_the_language() -> None:
    fact = _fact(
        FactKind.CATEGORICAL,
        "away",
        value_map={"ru": {"away": "от Солнца", "toward": "к Солнцу"}, "en": {"away": "away"}},
    )

    assert fact.display_value("ru") == "от Солнца"
    assert fact.display_value("en") == "away"


def test_categorical_value_missing_from_the_language_map_is_an_error() -> None:
    fact = _fact(FactKind.CATEGORICAL, "G3", value_map={"ru": {"G1": "слабая"}})

    with pytest.raises(ValueError, match="значения 'G3' нет в display.value_map"):
        fact.display_value("ru")


def test_categorical_without_map_for_the_language_is_shown_as_is() -> None:
    fact = _fact(FactKind.CATEGORICAL, "G1", value_map={"ru": {"G1": "слабая"}})

    assert fact.display_value("en") == "G1"


def test_interval_uses_the_number_policy_for_both_ends() -> None:
    fact = make_fact(kind=FactKind.INTERVAL, value=(347.9, 449.6))

    assert fact.display_value("ru") == "от 350 до 450"
    assert fact.display_value("en") == "from 350 to 450"


def test_interval_with_kp_formatter() -> None:
    fact = _fact(FactKind.INTERVAL, (0.667, 2.333), formatter="kp")

    assert fact.display_value("ru") == "от 1- до 2+"


def test_timestamp_is_shown_in_utc() -> None:
    fact = _fact(FactKind.TIMESTAMP, datetime(2026, 9, 1, 15, 38, tzinfo=MSK))

    assert fact.display_value("ru") == "2026-09-01 12:38 UTC"
    assert fact.display_value("en") == "2026-09-01 12:38 UTC"


def test_time_range() -> None:
    fact = _fact(FactKind.TIME_RANGE, (T_FROM, T_TO))

    assert fact.display_value("ru") == "с 2026-09-01 00:00 UTC по 2026-09-01 12:00 UTC"
    assert fact.display_value("en") == "from 2026-09-01 00:00 UTC to 2026-09-01 12:00 UTC"


def test_list_of_strings_and_ints() -> None:
    assert _fact(FactKind.LIST, ["4513", "4520"]).display_value() == "4513, 4520"
    assert _fact(FactKind.LIST, [4513, 4520]).display_value() == "4513, 4520"


def test_list_of_floats_uses_the_number_policy() -> None:
    fact = _fact(FactKind.LIST, [4.6, 7.2], round_to=1)

    assert fact.display_value() == "5, 7"


def test_list_of_floats_without_policy_is_an_error() -> None:
    with pytest.raises(ValueError, match="не задана политика отображения числа"):
        _fact(FactKind.LIST, [4.6]).display_value()


def test_list_of_booleans_uses_words() -> None:
    assert _fact(FactKind.LIST, [True, False]).display_value("ru") == "да, нет"


def test_empty_list_has_no_display_value() -> None:
    with pytest.raises(ValueError, match="пустой список не имеет отображения"):
        _fact(FactKind.LIST, []).display_value()


def test_distribution_display_is_not_defined_yet() -> None:
    fact = _fact(FactKind.DISTRIBUTION, {"quiet": 0.6, "G1": 0.4})

    with pytest.raises(NotImplementedError, match="отображение распределения не определено"):
        fact.display_value()


# --- canonical_statement ---


@pytest.mark.parametrize(
    ("fact_id", "expected"),
    [
        (
            "sw.speed.max_24h",
            "Максимальная скорость солнечного ветра за период составила 450 км/с.",
        ),
        ("sw.imf.bt", "Модуль межпланетного магнитного поля составил 5 нТл."),
        ("geo.kp.max_24h", "Максимальный Kp за период составил 2+."),
        ("geo.dst.min_24h", "Минимальное значение Dst за период составило -15 нТл."),
        ("flare.count_24h", "За период зарегистрировано вспышек: 8."),
        (
            "flare.max_class_24h",
            "Самая сильная вспышка в рентгеновском диапазоне за период — класса C5.7.",
        ),
        ("ar.count", "На диске наблюдалось активных областей: 6."),
        ("cme.earth_directed_count", "За период выявлено выбросов в направлении Земли: 0."),
        ("forecast.storm.level", "Ожидается слабая геомагнитная буря."),
    ],
)
def test_canonical_statement_ru(fact_id: str, expected: str) -> None:
    assert QUIET[fact_id].canonical_statement("ru") == expected
    assert QUIET[fact_id].canonical_statement() == expected  # язык по умолчанию — русский


@pytest.mark.parametrize(
    ("fact_id", "expected"),
    [
        ("sw.speed.max_24h", "Maximum solar wind speed over the period was 450 km/s."),
        ("sw.imf.bt", "The interplanetary magnetic field strength was 5 nT."),
        ("geo.kp.max_24h", "Maximum Kp over the period was 2+."),
        ("geo.dst.min_24h", "Minimum Dst over the period was -15 nT."),
        ("flare.count_24h", "8 flares were recorded in the period."),
        ("flare.max_class_24h", "The largest X-ray flare of the period was class C5.7."),
        ("ar.count", "6 active regions were present on the disk."),
        ("cme.earth_directed_count", "0 Earth-directed CMEs were identified in the period."),
        ("forecast.storm.level", "A minor geomagnetic storm is expected."),
    ],
)
def test_canonical_statement_en(fact_id: str, expected: str) -> None:
    assert QUIET[fact_id].canonical_statement("en") == expected


def test_canonical_statement_follows_the_value() -> None:
    fact = QUIET["sw.speed.max_24h"].with_value(347.9)

    assert (
        fact.canonical_statement("en") == "Maximum solar wind speed over the period was 350 km/s."
    )


def test_canonical_statement_needs_a_template_for_the_language() -> None:
    fact = make_fact(display=DisplayPolicy(round_to=25, statement={"ru": "Скорость {value}."}))

    assert fact.canonical_statement("ru") == "Скорость 450."
    with pytest.raises(ValueError, match="нет шаблона утверждения для языка 'en'"):
        fact.canonical_statement("en")


def test_canonical_statement_is_not_invented_without_a_template() -> None:
    with pytest.raises(ValueError, match="нет шаблона утверждения"):
        make_fact(display=DisplayPolicy(round_to=25)).canonical_statement()


def test_canonical_statement_of_a_fact_without_value_is_an_error() -> None:
    with pytest.raises(ValueError, match="не имеет значения"):
        make_missing_fact(
            display=DisplayPolicy(round_to=1, statement={"ru": "Пик потока {value}."})
        ).canonical_statement()


def test_canonical_statement_does_not_interpret_braces_in_the_value() -> None:
    fact = _fact(FactKind.CATEGORICAL, "{x}", statement={"ru": "Класс {value}."})

    assert fact.canonical_statement("ru") == "Класс {x}."


def test_canonical_statement_keeps_literal_braces_of_the_template() -> None:
    fact = make_fact(
        display=DisplayPolicy(round_to=25, statement={"en": "Raw {{value}} is {value}"})
    )

    assert fact.canonical_statement("en") == "Raw {value} is 450"


def test_canonical_statement_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="Неизвестный язык"):
        QUIET["ar.count"].canonical_statement("de")  # type: ignore[arg-type]


def test_degraded_fact_with_a_value_still_renders() -> None:
    """Статус качества учитывает вызывающий код (is_usable, правила планировщика), а не текст."""
    fact = QUIET["cme.earth_directed_count"]

    assert fact.qc is QCStatus.DEGRADED
    assert fact.is_usable()
    assert fact.canonical_statement("en") == "0 Earth-directed CMEs were identified in the period."
