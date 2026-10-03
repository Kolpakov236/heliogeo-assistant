"""Форматирование чисел для текста: округление до шага, Kp, форматтеры, язык."""

import pytest

from heliogeo.facts.formatting import (
    DEFAULT_LANG,
    FORMATTERS,
    LANGUAGES,
    check_lang,
    format_kp,
    format_number,
)

# --- format_number ---


@pytest.mark.parametrize(
    ("value", "round_to", "expected"),
    [
        # скорость ветра: шаг 25 км/с
        (449.6, 25, "450"),
        (347.9, 25, "350"),
        (412.4, 25, "400"),
        (412.5, 25, "425"),
        (462.5, 25, "475"),  # половина вверх, а не к чётному (банковское round дало бы 450)
        (437.5, 25, "450"),
        (955.0, 25, "950"),
        # поле: шаг 1 нТл
        (5.17, 1, "5"),
        (4.5, 1, "5"),
        (-47.85, 1, "-48"),
        (-4.5, 1, "-5"),  # половина «вверх» по модулю
        # Dst: шаг 5 нТл
        (-9, 5, "-10"),
        (-406, 5, "-405"),
        (-7, 5, "-5"),
        (-167, 5, "-165"),
        # без «минус нуля»
        (-0.4, 1, "0"),
        (-2, 5, "0"),
        # крупные значения без экспоненты и без «.0»
        (104000000.0, 1e6, "104000000"),
        (449, 25, "450"),  # int на входе
    ],
)
def test_format_number_rounds_to_step(value: float, round_to: float, expected: str) -> None:
    assert format_number(value, round_to=round_to, lang="en") == expected
    assert format_number(value, round_to=round_to, lang="ru") == expected


@pytest.mark.parametrize(
    ("value", "round_to", "expected_en"),
    [
        (2.5, 0.5, "2.5"),
        (1.26, 0.25, "1.25"),
        (0.05, 0.1, "0.1"),  # без двоичных артефактов float
        (0.04, 0.1, "0.0"),
        (4.084, 0.1, "4.1"),
    ],
)
def test_format_number_fractional_step_uses_language_separator(
    value: float, round_to: float, expected_en: str
) -> None:
    assert format_number(value, round_to=round_to, lang="en") == expected_en
    assert format_number(value, round_to=round_to, lang="ru") == expected_en.replace(".", ",")


def test_format_number_default_language_is_russian() -> None:
    assert DEFAULT_LANG == "ru"
    assert format_number(2.5, round_to=0.5) == "2,5"


@pytest.mark.parametrize("round_to", [0, -5, -0.1])
def test_format_number_rejects_non_positive_step(round_to: float) -> None:
    with pytest.raises(ValueError, match="положительным"):
        format_number(10, round_to=round_to)


# --- format_kp ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # трети из GFZ
        (0.0, "0"),
        (0.333, "0+"),
        (0.667, "1-"),
        (1.0, "1"),
        (1.333, "1+"),
        (1.667, "2-"),
        (2.333, "2+"),
        (2.667, "3-"),
        (3.0, "3"),
        (4.333, "4+"),
        (4.667, "5-"),
        (5.0, "5"),
        (8.667, "9-"),
        (9.0, "9"),
        # почасовые значения, округлённые до 0.1
        (0.7, "1-"),
        (1.3, "1+"),
        (1.7, "2-"),
        (2.3, "2+"),
        (8.3, "8+"),
        # границы: дробная часть < 0.2 — целое, < 0.5 — «+», иначе следующее целое с «-»
        (0.19, "0"),
        (0.2, "0+"),
        (0.49, "0+"),
        (0.5, "1-"),
    ],
)
def test_format_kp_three_step_notation(value: float, expected: str) -> None:
    assert format_kp(value) == expected


@pytest.mark.parametrize("value", [-0.1, 9.1, 10.0, float("nan"), float("inf")])
def test_format_kp_rejects_values_outside_scale(value: float) -> None:
    with pytest.raises(ValueError, match="Kp вне шкалы"):
        format_kp(value)


# --- форматтеры и язык ---


def test_named_formatters_registry() -> None:
    assert FORMATTERS["kp"] is format_kp


def test_named_formatters_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        FORMATTERS["x"] = format_kp  # type: ignore[index]


def test_languages() -> None:
    assert LANGUAGES == ("ru", "en")
    assert check_lang("ru") == "ru"
    assert check_lang("en") == "en"


@pytest.mark.parametrize("lang", ["de", "RU", "", "ru "])
def test_check_lang_rejects_unknown(lang: str) -> None:
    with pytest.raises(ValueError, match="Неизвестный язык"):
        check_lang(lang)
