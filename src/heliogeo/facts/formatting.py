"""Форматирование чисел для текста: округление до шага и именованные форматтеры.

Чистые функции без зависимости от модели факта. ``Fact.display_value`` только выбирает, какую из
них применить по ``DisplayPolicy``; сами правила округления лежат здесь, в одном месте.
"""

from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Literal

Lang = Literal["ru", "en"]

LANGUAGES: tuple[Lang, ...] = ("ru", "en")
DEFAULT_LANG: Lang = "ru"

_DECIMAL_SEPARATOR: Mapping[Lang, str] = {"ru": ",", "en": "."}


def check_lang(lang: str) -> Lang:
    """Убедиться, что язык поддерживается; иначе ``ValueError``."""
    if lang == "ru":
        return "ru"
    if lang == "en":
        return "en"
    raise ValueError(f"Неизвестный язык {lang!r}; поддерживаются: {', '.join(LANGUAGES)}")


def format_number(value: float, *, round_to: float, lang: Lang = DEFAULT_LANG) -> str:
    """Число, округлённое до ближайшего кратного ``round_to``, без «хвостов» вроде ``450.0``.

    Округление «половина вверх» (по модулю): 462.5 при шаге 25 даёт 475, а не 450, как у
    банковского ``round``. Вычисления идут в ``Decimal`` по кратчайшему представлению числа,
    поэтому нет двоичных артефактов: 0.05 при шаге 0.1 даёт 0,1 (``0.1`` для en).

    Args:
        value: конечное число.
        round_to: шаг округления (25 для скорости ветра, 1 для поля, 5 для Dst).
        lang: язык; определяет десятичный разделитель (запятая для ru, точка для en).
    """
    step = Decimal(repr(float(round_to)))
    if step <= 0:
        raise ValueError(f"Шаг округления должен быть положительным, получено {round_to!r}")
    rounded = (Decimal(repr(float(value))) / step).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    ) * step
    if rounded == 0:
        rounded = Decimal(0)  # без «-0»
    exponent = step.normalize().as_tuple().exponent
    decimals = max(0, -exponent) if isinstance(exponent, int) else 0
    return f"{rounded:.{decimals}f}".replace(".", _DECIMAL_SEPARATOR[lang])


def format_kp(value: float) -> str:
    """Kp в трёхбалльной записи: 2.333 даёт «2+», 2.667 даёт «3-», 3.0 даёт «3».

    API отдаёт Kp дробным (трети: 0.667, 1.333, 1.667, ...), а в текстах его пишут целым или с
    «+»/«-». Значение вне шкалы 0–9 не форматируется: это ошибка данных, а не повод округлять.
    """
    if not 0 <= value <= 9:
        raise ValueError(f"Kp вне шкалы 0-9: {value!r}")
    whole = int(value)
    fraction = value - whole
    if fraction < 0.2:
        return str(whole)
    if fraction < 0.5:
        return f"{whole}+"
    return f"{whole + 1}-"


# Именованные форматтеры для DisplayPolicy.formatter. Новый форматтер добавляется сюда.
FORMATTERS: Mapping[str, Callable[[float], str]] = MappingProxyType({"kp": format_kp})
