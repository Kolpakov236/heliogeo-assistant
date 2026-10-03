"""Интеграционные тесты таблицы observation_daily (миграция 0003, часть задачи B0-05)."""

from datetime import date, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from heliogeo.storage.models import ObservationDailyRecord
from tests.integration.helpers import make_alembic_config, tables
from tests.mocks.observation import (
    make_empty_observation_daily,
    make_observation_daily,
    make_storm_observation_daily,
)

pytestmark = pytest.mark.db

# Вид колонки -> (data_type, udt_name) в information_schema
KINDS = {
    "date": ("date", "date"),
    "float": ("double precision", "float8"),
    "floats": ("ARRAY", "_float8"),
    "int": ("integer", "int4"),
    "bool": ("boolean", "bool"),
    "text": ("text", "text"),
    "jsonb": ("jsonb", "jsonb"),
    "timestamptz": ("timestamp with time zone", "timestamptz"),
}

# (колонка, вид, допускает NULL) в порядке ЭТАП1 §6, с добавленными колонками
EXPECTED_COLUMNS = [
    ("d", "date", False),
    ("kp_max", "float", True),
    ("kp_values", "floats", True),
    ("kp_source", "text", True),
    ("dst_min", "float", True),
    ("dst_source", "text", True),
    ("sw_speed_min", "float", True),
    ("sw_speed_avg", "float", True),
    ("sw_speed_max", "float", True),
    ("sw_bt", "float", True),
    ("sw_bz_min", "float", True),
    ("sw_density_avg", "float", True),
    ("sw_sector", "text", True),
    ("sw_source", "text", True),
    ("flare_max_class", "text", True),
    ("flare_counts", "jsonb", True),
    ("ar_count", "int", True),
    ("ar_total_area", "int", True),
    ("ar_has_delta", "bool", True),
    ("ar_summary", "jsonb", True),
    ("cme_count", "int", True),
    ("cme_earth_directed", "int", True),
    ("cme_direction_unknown", "int", True),
    ("cme_speed_max", "float", True),
    ("cme_width_max", "float", True),
    ("cme_earth_directed_speed_max", "float", True),
    ("qc", "jsonb", True),
    ("raw_s3_key", "text", False),
    ("ingested_at", "timestamptz", False),
]


def _values(row: ObservationDailyRecord) -> dict[str, Any]:
    """Значения всех колонок, кроме заполняемой БД ingested_at."""
    return {
        column.name: getattr(row, column.name)
        for column in ObservationDailyRecord.__table__.columns
        if column.name != "ingested_at"
    }


def _insert_fails(session: Session, row: ObservationDailyRecord) -> None:
    """Вставка отклоняется базой (IntegrityError); транзакция теста остаётся рабочей."""
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(row)
        session.flush()


# --- схема ---


def test_columns_match_spec(migrated_engine: Engine) -> None:
    """Имена, порядок, типы и NULL-ность колонок соответствуют схеме."""
    query = text(
        "SELECT column_name, data_type, udt_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'observation_daily' "
        "ORDER BY ordinal_position"
    )
    with migrated_engine.connect() as connection:
        rows = connection.execute(query).all()
    expected = [(name, *KINDS[kind], nullable) for name, kind, nullable in EXPECTED_COLUMNS]
    assert [(n, dt, udt, nullable == "YES") for n, dt, udt, nullable in rows] == expected


def test_server_default_and_primary_key(migrated_engine: Engine) -> None:
    query = text(
        "SELECT column_name, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'observation_daily' "
        "AND column_default IS NOT NULL"
    )
    with migrated_engine.connect() as connection:
        assert dict(connection.execute(query).all()) == {"ingested_at": "now()"}
    pk = inspect(migrated_engine).get_pk_constraint("observation_daily")
    assert (pk["name"], pk["constrained_columns"]) == ("pk_observation_daily", ["d"])


def test_check_constraints_exist(migrated_engine: Engine) -> None:
    names = {c["name"] for c in inspect(migrated_engine).get_check_constraints("observation_daily")}
    assert names == {
        "ck_observation_daily_kp_values_len",
        "ck_observation_daily_ar_count",
        "ck_observation_daily_ar_total_area",
        "ck_observation_daily_cme_count",
        "ck_observation_daily_cme_earth_directed",
        "ck_observation_daily_cme_direction_unknown",
        "ck_observation_daily_cme_split",
        "ck_observation_daily_flare_counts_object",
        "ck_observation_daily_ar_summary_object",
        "ck_observation_daily_qc_object",
    }


# --- данные ---


@pytest.mark.parametrize(
    "factory",
    [make_observation_daily, make_storm_observation_daily],
    ids=["quiet", "storm"],
)
def test_row_roundtrip(session: Session, factory: Any) -> None:
    """Строка сохраняется и читается без потерь: массивы, JSONB, числа, NULL, дата."""
    row = factory()
    expected = _values(row)
    session.add(row)
    session.flush()
    session.expire(row)

    loaded = session.get(ObservationDailyRecord, expected["d"])
    assert loaded is not None
    assert _values(loaded) == expected


def test_day_without_data_keeps_nulls(session: Session) -> None:
    """«Нет данных» остаётся NULL, а не превращается в 0 или пустой массив."""
    session.add(make_empty_observation_daily())
    session.flush()
    loaded = session.scalars(select(ObservationDailyRecord)).one()
    others = {k: v for k, v in _values(loaded).items() if k not in {"d", "raw_s3_key"}}
    assert set(others.values()) == {None}


def test_ingested_at_is_filled_by_database(session: Session) -> None:
    row = make_observation_daily()
    session.add(row)
    session.flush()
    session.refresh(row)
    assert abs(session.scalar(select(func.now())) - row.ingested_at) < timedelta(minutes=1)


def test_one_row_per_date(session: Session) -> None:
    session.add(make_observation_daily())
    session.flush()
    _insert_fails(session, make_observation_daily(kp_max=1.0))


def test_neighbouring_dates_are_stored_side_by_side(session: Session) -> None:
    first = make_observation_daily()
    second = make_observation_daily(d=date(2026, 9, 2))
    session.add_all([first, second])
    session.flush()
    assert session.scalar(select(func.count()).select_from(ObservationDailyRecord)) == 2


def test_raw_key_is_mandatory(session: Session) -> None:
    """Сырьё сохраняется раньше разбора, поэтому строка без ключа сырья невозможна."""
    _insert_fails(session, make_observation_daily(raw_s3_key=None))


@pytest.mark.parametrize(
    "overrides",
    [
        {"kp_values": [1.0] * 9},
        {"kp_values": [1.0] * 24},  # «двойной Kp»: 24 значения из solar_wind.kp_obs
        {"ar_count": -1},
        {"ar_total_area": -1},
        {"cme_count": -1},
        {"cme_earth_directed": -1},
        {"cme_direction_unknown": -1},
        {"cme_count": 2, "cme_earth_directed": 2, "cme_direction_unknown": 1},
        {"flare_counts": [1, 2]},
        {"ar_summary": ["x"]},
        {"qc": "ok"},
    ],
    ids=[
        "kp-9-values",
        "kp-24-values",
        "ar-count-negative",
        "ar-area-negative",
        "cme-count-negative",
        "cme-earth-negative",
        "cme-unknown-negative",
        "cme-split-exceeds-count",
        "flare-counts-not-object",
        "ar-summary-not-object",
        "qc-not-object",
    ],
)
def test_check_constraints_reject_invalid_rows(session: Session, overrides: dict[str, Any]) -> None:
    _insert_fails(session, make_observation_daily(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"kp_values": [0.667, 1.0, 1.333]},  # неполные сутки: меньше 8 значений
        {"kp_values": []},
        {"cme_count": 3, "cme_earth_directed": 2, "cme_direction_unknown": 1},  # ровно по границе
        {"flare_counts": {}, "ar_summary": {}, "qc": {}},
    ],
    ids=["partial-day", "empty-array", "split-equals-count", "empty-objects"],
)
def test_boundary_values_are_accepted(session: Session, overrides: dict[str, Any]) -> None:
    session.add(make_observation_daily(**overrides))
    session.flush()


# --- откат миграции ---


def test_downgrade_removes_exactly_observation_daily(empty_db_url: str) -> None:
    cfg = make_alembic_config(empty_db_url)

    command.upgrade(cfg, "0003")
    with_table = tables(empty_db_url)
    command.downgrade(cfg, "0002")
    without_table = tables(empty_db_url)

    assert with_table - without_table == {"observation_daily"}
    assert {"forecast", "forecast_verification", "rolling_metrics"} <= without_table

    command.upgrade(cfg, "0003")
    assert "observation_daily" in tables(empty_db_url)
