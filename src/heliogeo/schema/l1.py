"""Схемы нормализованного слоя L1: семь сущностей, типы столбцов, единицы и правила для неизвестного.

L1 — первый слой, где данные источника приобретают схему. Схему определяет потребитель (признаки и
аналитика), а не тот, кто пишет разбор: иначе она будет удобна парсеру. Поэтому здесь описано, что
должно лежать в строке, а проверка ``validate_rows`` — это контрактный тест, который запускает
сторона, пишущая L1, на своих данных.

Правила, общие для всех сущностей:

* **Неизвестное — ``None``.** Пустое значение источника остаётся ``None`` и не превращается в ноль
  или пустую строку. Для столбцов, где физический ноль невозможен (скорость ветра, плотность,
  скорость и ширина выброса), ноль считается подменой неизвестного и отвергается.
* **Единицы — в названии столбца** (``area_msh``, ``speed_avg_km_s``), а не в комментарии.
  Допустимые единицы — словарь ``UNITS``; тест следит, что суффикс имени и единица совпадают.
* **Время — UTC с часовым поясом**, дата — ``date``, а не ``datetime``.
* **Происхождение в каждой строке:** ``source_product``, ``source_path`` (файл источника; ``None``, если
  значение пришло не из файла, например из live-потока) и ``raw_key`` (ключ тела ответа в бакете ``raw``), чтобы любое значение прослеживалось до байтов.
* **Раздел ``dt`` в файл не пишется:** это Hive-разметка пути (``heliogeo.storage.keys.key_l1``);
  дублирование столбца в файле сделало бы схему раздела неоднозначной.

Схемы описывают данные ответа ``/data`` (суточные агрегаты и списки событий). Числовая сетка
трёхдневного прогноза в ответе источника не приходит; для ``forecast_ext`` она добавится вместе с
новым разбором прогноза.
"""

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

# Единицы измерения: суффикс имени столбца -> что он означает.
UNITS: Final[Mapping[str, str]] = {
    "msh": "миллионные доли видимой полусферы Солнца",
    "deg": "градусы",
    "km_s": "километры в секунду",
    "cm3": "частиц в кубическом сантиметре",
    "nt": "нанотесла",
    "pfu": "частиц на см² в секунду на стерадиан (particle flux unit)",
    "cm2_sr_day": "частиц на см² на стерадиан за сутки (суточная флюенция)",
}


class ColumnType(StrEnum):
    """Тип значения столбца."""

    INT = "int"
    FLOAT = "float"
    STR = "str"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    DATE = "date"
    FLOAT_LIST = "float_list"
    STR_LIST = "str_list"


@dataclass(frozen=True, kw_only=True)
class Column:
    """Столбец сущности.

    Attributes:
        name: имя; единица измерения, если есть, — суффикс.
        type: тип значения.
        nullable: допустим ли ``None`` («неизвестно»).
        unit: ключ ``UNITS`` или ``None`` для безразмерных величин.
        description: что хранится.
        minimum: нижняя граница включительно (для списков — каждого элемента).
        maximum: верхняя граница включительно.
        zero_means_unknown: ноль в этом столбце невозможен физически, значит, это подменённое
            неизвестное; такое значение отвергается.
        pattern: регулярное выражение для строки (полное совпадение).
        allowed: допустимые строки.
        length: точная длина списка.
    """

    name: str
    type: ColumnType
    nullable: bool = True
    unit: str | None = None
    description: str
    minimum: float | None = None
    maximum: float | None = None
    zero_means_unknown: bool = False
    pattern: str | None = None
    allowed: tuple[str, ...] | None = None
    length: int | None = None


@dataclass(frozen=True, kw_only=True)
class EntitySpec:
    """Схема одной сущности L1.

    Attributes:
        name: имя сущности (совпадает с ``heliogeo.storage.keys.L1_ENTITIES``).
        description: что это за строки.
        key: столбцы, значения которых уникальны в разделе; пусто — не более одной строки за сутки.
        columns: все столбцы, включая столбцы происхождения.
    """

    name: str
    description: str
    key: tuple[str, ...]
    columns: tuple[Column, ...] = field(default_factory=tuple)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


# --- Конструкторы столбцов ---


def _col(name: str, kind: ColumnType, description: str, **options: Any) -> Column:
    return Column(name=name, type=kind, description=description, **options)


def _int(name: str, description: str, **options: Any) -> Column:
    return _col(name, ColumnType.INT, description, **options)


def _float(name: str, description: str, **options: Any) -> Column:
    return _col(name, ColumnType.FLOAT, description, **options)


def _str(name: str, description: str, **options: Any) -> Column:
    return _col(name, ColumnType.STR, description, **options)


def _bool(name: str, description: str, **options: Any) -> Column:
    return _col(name, ColumnType.BOOL, description, **options)


def _ts(name: str, description: str, **options: Any) -> Column:
    return _col(name, ColumnType.TIMESTAMP, description, **options)


_PROVENANCE = (
    _str("source_product", "продукт источника, из которого взята строка", nullable=False),
    _str("source_path", "путь файла источника; None, если значение пришло не из файла"),
    _str("raw_key", "ключ тела ответа в бакете raw", nullable=False),
)


def _entity(
    name: str, description: str, key: tuple[str, ...], columns: Sequence[Column]
) -> EntitySpec:
    return EntitySpec(name=name, description=description, key=key, columns=(*columns, *_PROVENANCE))


def _particle_channel(channel: str, label: str, *, with_alert: bool) -> list[Column]:
    columns = [
        _float(
            f"{channel}_fluence_cm2_sr_day",
            f"{label}: суточная флюенция",
            unit="cm2_sr_day",
            minimum=0,
        ),
        _float(
            f"{channel}_latest_pfu", f"{label}: последнее значение потока", unit="pfu", minimum=0
        ),
        _float(f"{channel}_peak_pfu", f"{label}: пик потока за сутки", unit="pfu", minimum=0),
        _float(f"{channel}_background_pfu", f"{label}: фоновый поток", unit="pfu", minimum=0),
    ]
    if with_alert:
        columns.append(_str(f"{channel}_alert", f"{label}: текст оповещения источника"))
    return columns


_KP = {"minimum": 0, "maximum": 9}

L1_SCHEMAS: Final[Mapping[str, EntitySpec]] = {
    spec.name: spec
    for spec in (
        _entity(
            "active_regions",
            "Активные области из суточного отчёта SRS: одна строка на область и дату наблюдения",
            ("observed_date", "noaa_number"),
            [
                _int("noaa_number", "номер области NOAA", nullable=False, minimum=1),
                _col("observed_date", ColumnType.DATE, "дата наблюдения", nullable=False),
                _ts("issued_utc", "время выпуска отчёта"),
                _str("location", "гелиографические координаты, например N27W92"),
                _int(
                    "carrington_lon_deg",
                    "кэррингтоновская долгота",
                    unit="deg",
                    minimum=0,
                    maximum=360,
                ),
                _int("area_msh", "площадь пятен", unit="msh", minimum=0),
                _int("extent_deg", "протяжённость по долготе", unit="deg", minimum=0, maximum=180),
                _int("spots", "число пятен", minimum=0),
                _str("mcintosh", "класс по Макинтошу"),
                _str("magnetic", "магнитный класс"),
                _str("growth_status", "динамика относительно предыдущего отчёта"),
                _int("growth_area_delta_msh", "изменение площади", unit="msh"),
                _int("growth_spots_delta", "изменение числа пятен"),
            ],
        ),
        _entity(
            "flares",
            "Рентгеновские вспышки из списка событий: одна строка на вспышку",
            ("max_utc", "flare_class"),
            [
                _ts("begin_utc", "начало"),
                _ts("max_utc", "максимум", nullable=False),
                _ts("end_utc", "конец"),
                _str(
                    "flare_class",
                    "класс, например X5.8",
                    nullable=False,
                    pattern=r"[ABCMX][0-9]+(\.[0-9]+)?",
                ),
                _int("active_region", "номер активной области", minimum=1),
                _str("location", "гелиографические координаты"),
                _str("observatory", "прибор или обсерватория"),
                _col("radio", ColumnType.STR_LIST, "радиоэффекты, как в источнике"),
            ],
        ),
        _entity(
            "cmes",
            "Корональные выбросы массы (каталог DONKI): одна строка на выброс",
            ("cme_id",),
            [
                _str("cme_id", "идентификатор выброса в каталоге", nullable=False),
                _ts("time_utc", "время первого появления", nullable=False),
                _float(
                    "speed_km_s",
                    "скорость",
                    unit="km_s",
                    minimum=0,
                    maximum=4000,
                    zero_means_unknown=True,
                ),
                _float(
                    "width_deg",
                    "угловая ширина",
                    unit="deg",
                    minimum=0,
                    maximum=360,
                    zero_means_unknown=True,
                ),
                _float(
                    "half_angle_deg",
                    "половинный угол",
                    unit="deg",
                    minimum=0,
                    maximum=180,
                    zero_means_unknown=True,
                ),
                _float("lat_deg", "широта направления", unit="deg", minimum=-90, maximum=90),
                _float("lon_deg", "долгота направления", unit="deg", minimum=-360, maximum=360),
                _str("cme_type", "тип выброса по каталогу"),
                _bool("earth_directed", "направлен ли к Земле (оценка каталога)"),
                _str("catalog", "каталог"),
                _str("source_location", "гелиографические координаты источника"),
                _int("active_region", "номер активной области", minimum=1),
                _str("note", "примечание каталога"),
            ],
        ),
        _entity(
            "particles",
            "Заряженные частицы за сутки: флюенции и потоки по каналам, признаки протонного события",
            (),
            [
                *_particle_channel("electron_gt2mev", "электроны >2 МэВ", with_alert=True),
                *_particle_channel("proton_gt10mev", "протоны >10 МэВ", with_alert=False),
                *_particle_channel("proton_gt100mev", "протоны >100 МэВ", with_alert=False),
                _str("sep_s_scale", "шкала S солнечной радиационной бури", pattern=r"S[1-5]"),
                _bool("sep_gt10_event", "было ли событие протонов >10 МэВ"),
                _bool("sep_gt100_event", "было ли событие протонов >100 МэВ"),
                _col("sep_alert_flags", ColumnType.STR_LIST, "флаги оповещений"),
                _str("sep_note", "примечание источника"),
            ],
        ),
        _entity(
            "solar_wind",
            "Солнечный ветер и межпланетное поле за сутки (агрегаты минутного ряда)",
            (),
            [
                _int("n_minutes", "число минутных значений в сутках", minimum=0, maximum=1440),
                _float(
                    "speed_min_km_s",
                    "скорость, минимум",
                    unit="km_s",
                    minimum=0,
                    maximum=3000,
                    zero_means_unknown=True,
                ),
                _float(
                    "speed_avg_km_s",
                    "скорость, среднее",
                    unit="km_s",
                    minimum=0,
                    maximum=3000,
                    zero_means_unknown=True,
                ),
                _float(
                    "speed_max_km_s",
                    "скорость, максимум",
                    unit="km_s",
                    minimum=0,
                    maximum=3000,
                    zero_means_unknown=True,
                ),
                _float(
                    "density_latest_cm3",
                    "плотность, последнее значение",
                    unit="cm3",
                    minimum=0,
                    maximum=1000,
                    zero_means_unknown=True,
                ),
                _float(
                    "density_avg_cm3",
                    "плотность, среднее",
                    unit="cm3",
                    minimum=0,
                    maximum=1000,
                    zero_means_unknown=True,
                ),
                _float(
                    "bt_nt",
                    "модуль поля Bt по полю imf.bt источника (что именно это за значение, не подтверждено)",
                    unit="nt",
                    minimum=0,
                    maximum=500,
                ),
                _float("bt_avg_nt", "модуль поля Bt, среднее", unit="nt", minimum=0, maximum=500),
                _float("bz_min_nt", "Bz, минимум", unit="nt", minimum=-500, maximum=500),
                _float("bz_max_nt", "Bz, максимум", unit="nt", minimum=-500, maximum=500),
                _float(
                    "bz_latest_nt", "Bz, последнее значение", unit="nt", minimum=-500, maximum=500
                ),
                _float("phi_deg", "азимут поля", unit="deg", minimum=0, maximum=360),
                _str("sector", "сектор межпланетного поля", allowed=("toward", "away")),
                _str("wind_source", "ряд, из которого посчитаны агрегаты, например omni_hro_1min"),
            ],
        ),
        _entity(
            "geomag_obs",
            "Геомагнитные наблюдения за сутки: трёхчасовой Kp и Dst",
            (),
            [
                _col(
                    "kp_3h",
                    ColumnType.FLOAT_LIST,
                    "трёхчасовой Kp за сутки, 8 значений; неизвестное — None",
                    length=8,
                    **_KP,
                ),
                _float("kp_max", "максимальный Kp за сутки", **_KP),
                _float("kp_est_now", "оценка Kp на момент запроса", **_KP),
                _str("kp_source", "источник Kp, например gfz"),
                _float(
                    "dst_latest_nt",
                    "Dst, последнее значение",
                    unit="nt",
                    minimum=-2000,
                    maximum=500,
                ),
                _float(
                    "dst_min_nt", "Dst, минимум за сутки", unit="nt", minimum=-2000, maximum=500
                ),
            ],
        ),
        _entity(
            "forecast_ext",
            "Внешний (SWPC) прогноз: уровни шкал на ближайшие сутки по последнему выпуску",
            (),
            [
                _ts("issued_utc", "время выпуска прогноза"),
                _str("r_scale", "шкала R, радиовсплески", pattern=r"R[1-5]"),
                _str("s_scale", "шкала S, радиационные бури", pattern=r"S[1-5]"),
                _str(
                    "g_scale",
                    "шкала G, магнитные бури; None — бури не ожидается",
                    pattern=r"G[1-5]",
                ),
            ],
        ),
    )
}


# --- Проверка строк ---


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_utc(value: Any) -> bool:
    return isinstance(value, datetime) and value.utcoffset() == timedelta(0)


def _check_number(column: Column, value: float, label: str) -> list[str]:
    problems: list[str] = []
    if column.zero_means_unknown and value == 0:
        problems.append(f"{label}: ноль вместо None (физически невозможное значение)")
    if column.minimum is not None and value < column.minimum:
        problems.append(f"{label}: {value} меньше {column.minimum}")
    if column.maximum is not None and value > column.maximum:
        problems.append(f"{label}: {value} больше {column.maximum}")
    return problems


def _check_text(column: Column, value: str, label: str) -> list[str]:
    if not value.strip():
        return [f"{label}: пустая строка вместо None"]
    if column.allowed is not None and value not in column.allowed:
        return [f"{label}: {value!r} не из {column.allowed}"]
    if column.pattern is not None and not re.fullmatch(column.pattern, value):
        return [f"{label}: {value!r} не подходит под {column.pattern}"]
    return []


def _check_value(column: Column, value: Any, label: str) -> list[str]:
    kind = column.type
    if kind is ColumnType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{label}: ожидалось целое, получено {value!r}"]
        return _check_number(column, value, label)
    if kind is ColumnType.FLOAT:
        if not _is_number(value):
            return [f"{label}: ожидалось конечное число, получено {value!r}"]
        return _check_number(column, value, label)
    if kind is ColumnType.STR:
        if not isinstance(value, str):
            return [f"{label}: ожидалась строка, получено {value!r}"]
        return _check_text(column, value, label)
    if kind is ColumnType.BOOL:
        return [] if isinstance(value, bool) else [f"{label}: ожидалось bool, получено {value!r}"]
    if kind is ColumnType.TIMESTAMP:
        if not _is_utc(value):
            return [f"{label}: ожидалось время UTC с часовым поясом, получено {value!r}"]
        return []
    if kind is ColumnType.DATE:
        if isinstance(value, datetime) or not isinstance(value, date):
            return [f"{label}: ожидалась дата (date), получено {value!r}"]
        return []
    return _check_list(column, value, label)


def _check_list(column: Column, value: Any, label: str) -> list[str]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return [f"{label}: ожидался список, получено {value!r}"]
    problems: list[str] = []
    if column.length is not None and len(value) != column.length:
        problems.append(f"{label}: длина {len(value)}, ожидалась {column.length}")
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if column.type is ColumnType.STR_LIST:
            if not isinstance(item, str):
                problems.append(f"{item_label}: ожидалась строка, получено {item!r}")
            elif not item.strip():
                problems.append(f"{item_label}: пустая строка")
        elif item is not None:
            if not _is_number(item):
                problems.append(
                    f"{item_label}: ожидалось конечное число или None, получено {item!r}"
                )
            else:
                problems += _check_number(column, item, item_label)
    return problems


def validate_row(entity: str, row: Mapping[str, Any]) -> list[str]:
    """Проблемы одной строки; пустой список — строка соответствует схеме.

    Raises:
        KeyError: если сущности нет в ``L1_SCHEMAS``.
    """
    spec = L1_SCHEMAS[entity]
    problems: list[str] = []
    names = set(spec.column_names)
    problems += [f"{entity}.{name}: лишний столбец" for name in sorted(set(row) - names)]
    for column in spec.columns:
        label = f"{entity}.{column.name}"
        if column.name not in row:
            problems.append(f"{label}: нет столбца (неизвестное записывается как None)")
            continue
        value = row[column.name]
        if value is None:
            if not column.nullable:
                problems.append(f"{label}: обязательный столбец равен None")
            continue
        problems += _check_value(column, value, label)
    return problems


def validate_rows(entity: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Проблемы всех строк одного раздела (одних суток): схема, уникальность ключа.

    Raises:
        KeyError: если сущности нет в ``L1_SCHEMAS``.
    """
    spec = L1_SCHEMAS[entity]
    problems: list[str] = []
    for index, row in enumerate(rows):
        problems += [f"строка {index}: {problem}" for problem in validate_row(entity, row)]
    if not spec.key:
        if len(rows) > 1:
            problems.append(
                f"{entity}: за сутки ожидается не более одной строки, получено {len(rows)}"
            )
        return problems
    keys = Counter(tuple(row.get(name) for name in spec.key) for row in rows)
    problems += [
        f"{entity}: ключ {spec.key} повторяется: {key}" for key, n in keys.items() if n > 1
    ]
    return problems
