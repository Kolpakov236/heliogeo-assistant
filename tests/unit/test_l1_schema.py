"""Схемы L1: эталонные строки проходят, подсунутые дефекты ловятся, единицы и имена согласованы.

Это контрактные тесты стороны, пишущей L1: ``validate_rows`` запускается на её данных, а здесь
проверено, что сама проверка не пропускает типичные дефекты.
"""

import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from heliogeo.schema.l1 import L1_SCHEMAS, UNITS, ColumnType, validate_row, validate_rows
from heliogeo.storage.keys import L1_ENTITIES
from tests.mocks.l1 import make_l1_row

ENTITIES = sorted(L1_SCHEMAS)
DOC = Path(__file__).resolve().parents[2] / "docs" / "lake.md"


def test_schemas_cover_exactly_the_entities_of_the_key_builders() -> None:
    assert set(L1_SCHEMAS) == set(L1_ENTITIES)
    assert all(spec.name == name for name, spec in L1_SCHEMAS.items())


@pytest.mark.parametrize("entity", ENTITIES)
def test_the_reference_row_is_valid(entity: str) -> None:
    assert validate_row(entity, make_l1_row(entity)) == []
    assert validate_rows(entity, [make_l1_row(entity)]) == []


@pytest.mark.parametrize("entity", ENTITIES)
def test_every_column_has_a_description_and_provenance_is_required(entity: str) -> None:
    spec = L1_SCHEMAS[entity]
    assert all(column.description for column in spec.columns)
    for name in ("source_product", "source_path", "raw_key"):
        column = next(c for c in spec.columns if c.name == name)
        assert column.nullable is False


@pytest.mark.parametrize("entity", ENTITIES)
def test_unit_is_the_name_suffix_and_comes_from_the_dictionary(entity: str) -> None:
    """Единицы — в названии столбца, а не в комментарии."""
    for column in L1_SCHEMAS[entity].columns:
        if column.unit is None:
            matches = [u for u in UNITS if column.name.endswith(f"_{u}")]
            assert not matches, f"{column.name} выглядит размерным, но unit не задан"
        else:
            assert column.unit in UNITS
            assert column.name.endswith(f"_{column.unit}"), column.name


@pytest.mark.parametrize("entity", ENTITIES)
def test_key_columns_exist_and_are_required(entity: str) -> None:
    spec = L1_SCHEMAS[entity]
    for name in spec.key:
        column = next(c for c in spec.columns if c.name == name)
        assert column.nullable is False


@pytest.mark.parametrize("entity", ENTITIES)
def test_a_missing_or_extra_column_is_reported(entity: str) -> None:
    row = make_l1_row(entity)
    name = next(c.name for c in L1_SCHEMAS[entity].columns if c.nullable)

    del row[name]
    assert any("нет столбца" in p for p in validate_row(entity, row))

    row = make_l1_row(entity, surprise=1)
    assert any("лишний столбец" in p for p in validate_row(entity, row))


@pytest.mark.parametrize("entity", ENTITIES)
def test_a_required_column_cannot_be_none(entity: str) -> None:
    for column in L1_SCHEMAS[entity].columns:
        if not column.nullable:
            problems = validate_row(entity, make_l1_row(entity, **{column.name: None}))
            assert any("обязательный" in p for p in problems), column.name


@pytest.mark.parametrize("entity", ENTITIES)
def test_unknown_stays_none_in_every_nullable_column(entity: str) -> None:
    unknown = {c.name: None for c in L1_SCHEMAS[entity].columns if c.nullable}
    assert validate_row(entity, make_l1_row(entity, **unknown)) == []


WRONG_TYPES = [
    ("active_regions", "noaa_number", "3664"),
    ("active_regions", "noaa_number", True),
    ("active_regions", "noaa_number", 3664.0),
    ("active_regions", "observed_date", datetime(2024, 5, 11, tzinfo=UTC)),
    ("active_regions", "observed_date", "2024-05-11"),
    ("flares", "max_utc", datetime(2024, 5, 11, 1, 23)),
    ("flares", "max_utc", datetime(2024, 5, 11, 1, 23, tzinfo=timezone(timedelta(hours=3)))),
    ("flares", "max_utc", "2024-05-11T01:23:00Z"),
    ("cmes", "earth_directed", 1),
    ("cmes", "speed_km_s", "1263"),
    ("cmes", "speed_km_s", True),
    ("cmes", "speed_km_s", math.nan),
    ("cmes", "speed_km_s", math.inf),
    ("flares", "radio", "CTM"),
    ("flares", "radio", ["CTM", 1]),
    ("geomag_obs", "kp_3h", "9"),
    ("geomag_obs", "kp_3h", [9.0, "8", None, None, None, None, None, None]),
    ("solar_wind", "n_minutes", 1440.0),
]


@pytest.mark.parametrize(("entity", "column", "value"), WRONG_TYPES)
def test_a_wrong_type_is_caught(entity: str, column: str, value: Any) -> None:
    assert validate_row(entity, make_l1_row(entity, **{column: value})) != []


ZERO_FOR_UNKNOWN = [
    ("solar_wind", "speed_avg_km_s"),
    ("solar_wind", "speed_min_km_s"),
    ("solar_wind", "density_avg_cm3"),
    ("solar_wind", "density_latest_cm3"),
    ("cmes", "speed_km_s"),
    ("cmes", "width_deg"),
    ("cmes", "half_angle_deg"),
]


@pytest.mark.parametrize(("entity", "column"), ZERO_FOR_UNKNOWN)
def test_zero_instead_of_none_is_caught(entity: str, column: str) -> None:
    """Неизвестная скорость, превращённая в 0 км/с, — дефект, из-за которого и нужен L1."""
    for zero in (0, 0.0):
        problems = validate_row(entity, make_l1_row(entity, **{column: zero}))
        assert any("ноль вместо None" in p for p in problems)
    assert validate_row(entity, make_l1_row(entity, **{column: None})) == []


def test_zero_is_fine_where_it_is_physically_possible() -> None:
    assert validate_row("active_regions", make_l1_row("active_regions", area_msh=0, spots=0)) == []
    assert validate_row("geomag_obs", make_l1_row("geomag_obs", kp_max=0.0)) == []
    assert validate_row("solar_wind", make_l1_row("solar_wind", bz_latest_nt=0.0)) == []


def test_an_empty_string_is_not_a_value() -> None:
    for entity, column in [
        ("flares", "observatory"),
        ("cmes", "note"),
        ("geomag_obs", "kp_source"),
    ]:
        problems = validate_row(entity, make_l1_row(entity, **{column: "  "}))
        assert any("пустая строка" in p for p in problems)
    problems = validate_row("flares", make_l1_row("flares", radio=["CTM", ""]))
    assert any("пустая строка" in p for p in problems)


RANGES_AND_FORMATS = [
    ("geomag_obs", "kp_max", 9.5),
    ("geomag_obs", "kp_max", -0.1),
    ("geomag_obs", "kp_3h", [9.0, 10.0, None, None, None, None, None, None]),
    ("geomag_obs", "kp_3h", [9.0, 8.0]),
    ("geomag_obs", "dst_min_nt", -5000.0),
    ("solar_wind", "sector", "sideways"),
    ("solar_wind", "phi_deg", 361.0),
    ("solar_wind", "n_minutes", 1441),
    ("flares", "flare_class", "X"),
    ("flares", "flare_class", "x5.8"),
    ("forecast_ext", "g_scale", "G0"),
    ("forecast_ext", "g_scale", "G6"),
    ("forecast_ext", "s_scale", "G1"),
    ("active_regions", "noaa_number", 0),
    ("active_regions", "carrington_lon_deg", 400),
    ("cmes", "lat_deg", 91.0),
]


@pytest.mark.parametrize(("entity", "column", "value"), RANGES_AND_FORMATS)
def test_values_outside_the_range_or_format_are_caught(
    entity: str, column: str, value: Any
) -> None:
    assert validate_row(entity, make_l1_row(entity, **{column: value})) != []


def test_no_storm_expected_is_none_and_not_g0() -> None:
    """Прогноз «бури не ожидается» записывается как None: уровня G0 в шкале нет."""
    assert validate_row("forecast_ext", make_l1_row("forecast_ext", g_scale=None)) == []
    assert validate_row("forecast_ext", make_l1_row("forecast_ext", g_scale="G0")) != []


def test_unknown_kp_inside_the_three_hour_list_is_allowed() -> None:
    kp = [9.0, None, 8.3, None, None, None, None, None]
    assert validate_row("geomag_obs", make_l1_row("geomag_obs", kp_3h=kp)) == []


def test_repeated_key_in_one_partition_is_caught() -> None:
    row = make_l1_row("flares")
    problems = validate_rows("flares", [row, dict(row)])
    assert any("повторяется" in p for p in problems)
    other = make_l1_row("flares", flare_class="M3.1")
    assert validate_rows("flares", [row, other]) == []


@pytest.mark.parametrize("entity", ["particles", "solar_wind", "geomag_obs", "forecast_ext"])
def test_daily_entities_take_at_most_one_row_per_day(entity: str) -> None:
    row = make_l1_row(entity)
    assert validate_rows(entity, []) == []
    assert any("не более одной" in p for p in validate_rows(entity, [row, dict(row)]))


def test_problems_name_the_row_and_the_column() -> None:
    bad = make_l1_row("cmes", cme_id="other", speed_km_s=0)
    problems = validate_rows("cmes", [make_l1_row("cmes"), bad])

    assert problems == [
        "строка 1: cmes.speed_km_s: ноль вместо None (физически невозможное значение)"
    ]


def test_unknown_entity_is_an_error() -> None:
    with pytest.raises(KeyError):
        validate_row("sunspots", {})


def test_list_columns_are_typed_as_lists() -> None:
    kinds = {c.name: c.type for spec in L1_SCHEMAS.values() for c in spec.columns}
    assert kinds["kp_3h"] is ColumnType.FLOAT_LIST
    assert kinds["radio"] is ColumnType.STR_LIST
    assert kinds["observed_date"] is ColumnType.DATE
    assert kinds["max_utc"] is ColumnType.TIMESTAMP


def test_the_documentation_lists_every_column_of_every_entity() -> None:
    """Документ не должен отставать от схемы: каждый столбец упомянут в docs/lake.md."""
    text = DOC.read_text(encoding="utf-8")
    missing = [
        f"{entity}.{column.name}"
        for entity, spec in L1_SCHEMAS.items()
        for column in spec.columns
        if f"`{column.name}`" not in text
    ]
    assert missing == []
    assert all(f"`{unit}`" in text for unit in UNITS)
