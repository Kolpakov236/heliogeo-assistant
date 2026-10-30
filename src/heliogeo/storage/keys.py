"""Построители ключей Object Storage: единственное место, где пути объектов собираются из частей.

Если один разработчик напишет ``dt=2026-10-05``, а другой ``date=2026-10-05``, Yandex Query увидит
две разные схемы разделов и молча прочитает половину данных. Поэтому остальной код ключи не
склеивает, а вызывает функции отсюда; тест ``test_object_keys.py`` следит, что разметка ``dt=`` не
встречается нигде больше.

Ключ — путь внутри бакета. Бакетов четыре (см. ``Bucket``), имя бакета — ``{префикс}-{бакет}``.
Все части проверяются: ключ с ``..``, пробелом или двоеточием отвергается, а не «чинится», потому
что то же имя должно быть допустимо и в Object Storage, и в локальной файловой системе Windows.
"""

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final, Literal

# Сущности нормализованного слоя L1 (по одной таблице на сущность).
L1_ENTITIES: Final = (
    "active_regions",
    "flares",
    "cmes",
    "particles",
    "solar_wind",
    "geomag_obs",
    "forecast_ext",
)
# Слой L2: признаки на момент времени, цели, копия прогнозов для аналитики.
L2_KINDS: Final = ("features", "targets", "forecasts")

# Имя бакета в Yandex Object Storage: 3–63 символа, строчные буквы, цифры и дефис.
_PREFIX = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]")
_BUCKET_NAME_MAX = 63
# Часть пути: начинается с буквы или цифры, поэтому «.» и «..» невозможны.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=+-]*")
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._=+-]*")
_PRODUCT = re.compile(r"[a-z0-9][a-z0-9_]*")
_HHMM = re.compile(r"(?:[01][0-9]|2[0-3])[0-5][0-9]")


class Bucket(StrEnum):
    """Бакеты проекта; значение — суффикс имени бакета."""

    RAW = "raw"
    LAKE = "lake"
    ARTIFACTS = "artifacts"
    ISSUES = "issues"


def bucket_name(prefix: str, bucket: Bucket) -> str:
    """Имя бакета ``{prefix}-{bucket}``.

    Raises:
        ValueError: если префикс или итоговое имя не подходят под правила имён бакетов.
    """
    name = f"{prefix}-{bucket.value}"
    if not _PREFIX.fullmatch(prefix) or not 3 <= len(name) <= _BUCKET_NAME_MAX:
        raise ValueError(
            f"префикс бакета должен состоять из строчных латинских букв, цифр и дефисов, "
            f"имя бакета — не длиннее {_BUCKET_NAME_MAX} символов, получено {prefix!r}"
        )
    return name


def validate_key(key: str) -> str:
    """Проверить готовый ключ: части через ``/``, без ``..`` и пустых частей.

    Нужна хранилищам для ключей, которые пришли не из построителей. Подчёркивание в начале части
    допустимо: так начинается ``_manifest``.

    Raises:
        ValueError: если ключ не годится.
    """
    if not key or not all(_KEY_SEGMENT.fullmatch(part) for part in key.split("/")):
        raise ValueError(f"ключ объекта {key!r} не годится")
    return key


def _segment(value: str, what: str) -> str:
    if not _SEGMENT.fullmatch(value):
        raise ValueError(f"{what} {value!r} не годится для ключа объекта")
    return value


def _partition(day: date) -> str:
    return f"dt={day.isoformat()}"


def _part_file(part: int) -> str:
    if part < 0 or part > 999:
        raise ValueError(f"номер части должен быть от 0 до 999, получено {part}")
    return f"part-{part:03d}.parquet"


def _choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"{what} {value!r} неизвестна, допустимы: {', '.join(allowed)}")
    return value


# --- Бакет raw: сырьё ---


def key_raw_warehouse(product: str, year: int, filename: str) -> str:
    """Файл источника как получен: ``warehouse/{product}/{YYYY}/{filename}``.

    Раскладка повторяет каталоги самого источника один в один, поэтому архив источника
    синхронизируется в бакет как есть.
    """
    if not _PRODUCT.fullmatch(product):
        raise ValueError(f"продукт {product!r} не годится для ключа объекта")
    if not 1900 <= year <= 2999:
        raise ValueError(f"год должен быть четырёхзначным, получено {year}")
    return f"warehouse/{product}/{year:04d}/{_segment(filename, 'имя файла')}"


def key_raw_live(day: date, hhmm: str, name: str) -> str:
    """Снимок live-эндпоинта: ``live/{YYYY-MM-DD}/{HHMM}/{name}``.

    ``hhmm`` — время снимка в UTC, ``0000``…``2359``.
    """
    if not _HHMM.fullmatch(hhmm):
        raise ValueError(f"время снимка должно быть вида HHMM, получено {hhmm!r}")
    return f"live/{day.isoformat()}/{hhmm}/{_segment(name, 'имя снимка')}"


def key_raw_snapshot(
    endpoint: Literal["data", "text"], requested_at: datetime, window: str | None, no_live: bool
) -> str:
    """Тело ответа API источника: ``live/{дата}/{HHMM}/{endpoint}_{окно}_{live|nolive}.json``.

    Дата и время — запрошенный момент в UTC. Окно и режим живых данных входят в имя, потому что
    разные окна и ``no_live`` дают разные ответы на один и тот же момент. У ``text`` окна нет.

    Raises:
        ValueError: если момент без часового пояса или окно не годится для имени.
    """
    if requested_at.tzinfo is None or requested_at.utcoffset() is None:
        raise ValueError(f"момент запроса должен быть с часовым поясом, получено {requested_at!r}")
    moment = requested_at.astimezone(UTC)
    mode = "nolive" if no_live else "live"
    parts = [endpoint, mode] if endpoint == "text" else [endpoint, window or "ut_day", mode]
    return key_raw_live(moment.date(), f"{moment:%H%M}", "_".join(parts) + ".json")


def key_manifest(day: date) -> str:
    """Манифест скачанного за дату: ``_manifest/{YYYY-MM-DD}.json``."""
    return f"_manifest/{day.isoformat()}.json"


# --- Бакет lake: Parquet ---


def key_l1(entity: str, day: date, part: int = 0) -> str:
    """Нормализованная сущность: ``l1/{entity}/dt={YYYY-MM-DD}/part-NNN.parquet``."""
    _choice(entity, L1_ENTITIES, "сущность L1")
    return f"l1/{entity}/{_partition(day)}/{_part_file(part)}"


def key_l2(kind: str, day: date, part: int = 0) -> str:
    """Слой L2: ``l2/{kind}/dt={YYYY-MM-DD}/part-NNN.parquet``."""
    _choice(kind, L2_KINDS, "вид данных L2")
    return f"l2/{kind}/{_partition(day)}/{_part_file(part)}"


def key_corpus(name: str) -> str:
    """Производные тексты корпуса (индекс выпусков текста): ``corpus/{name}``."""
    return f"corpus/{_segment(name, 'имя объекта корпуса')}"


# --- Бакеты issues и artifacts ---


def key_issue(day: date, run_id: str, name: str) -> str:
    """Артефакт выпуска: ``{YYYY-MM-DD}/{run_id}/{name}``.

    ``run_id`` бывает составным (``{время}/{kind}/{ULID}``): каждая его часть проверяется отдельно,
    поэтому время запуска записывается без двоеточий (``20261005T031422Z``).
    """
    parts = [_segment(part, "часть run_id") for part in run_id.split("/")]
    return "/".join([day.isoformat(), *parts, _segment(name, "имя файла")])


def key_artifact(kind: str, *parts: str) -> str:
    """Модельный артефакт: ``{kind}/{part}/…``, например ``models/m0/2026.10.01/model.json``."""
    if not parts:
        raise ValueError("у ключа артефакта должна быть хотя бы одна часть после вида")
    return "/".join([_segment(kind, "вид артефакта"), *(_segment(p, "часть ключа") for p in parts)])
