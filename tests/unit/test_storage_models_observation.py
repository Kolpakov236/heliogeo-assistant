"""Тесты модели observation_daily без БД: структура таблицы и DDL."""

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from heliogeo.storage.models import Base, ObservationDailyRecord

TABLE: Table = Base.metadata.tables["observation_daily"]

# Порядок колонок — как в ЭТАП1 §6, с добавленными (!) колонками на своих местах
EXPECTED_COLUMNS = [
    "d",
    "kp_max",
    "kp_values",
    "kp_source",
    "dst_min",
    "dst_source",
    "sw_speed_min",
    "sw_speed_avg",
    "sw_speed_max",
    "sw_bt",
    "sw_bz_min",
    "sw_density_avg",
    "sw_sector",
    "sw_source",
    "flare_max_class",
    "flare_counts",
    "ar_count",
    "ar_total_area",
    "ar_has_delta",
    "ar_summary",
    "cme_count",
    "cme_earth_directed",
    "cme_direction_unknown",
    "cme_speed_max",
    "cme_width_max",
    "cme_earth_directed_speed_max",
    "qc",
    "raw_s3_key",
    "ingested_at",
]


def _ddl() -> str:
    return str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))


def test_model_is_registered_in_metadata() -> None:
    assert ObservationDailyRecord.__tablename__ == "observation_daily"
    assert Base.metadata.tables["observation_daily"] is TABLE


def test_columns_and_order_follow_the_specification() -> None:
    assert [c.name for c in TABLE.columns] == EXPECTED_COLUMNS


def test_primary_key_is_the_date() -> None:
    assert [c.name for c in TABLE.primary_key.columns] == ["d"]


def test_only_date_and_raw_key_and_ingestion_time_are_required() -> None:
    """Всё остальное допускает NULL: «нет данных» не подменяется нулём (РУК §2.2)."""
    required = {c.name for c in TABLE.columns if not c.nullable}
    assert required == {"d", "raw_s3_key", "ingested_at"}


def test_ddl_has_expected_types() -> None:
    ddl = _ddl()
    for fragment in (
        "d DATE NOT NULL",
        "kp_values DOUBLE PRECISION[]",
        "sw_density_avg DOUBLE PRECISION",
        "sw_sector TEXT",
        "flare_counts JSONB",
        "ar_count INTEGER",
        "ar_has_delta BOOLEAN",
        "cme_earth_directed_speed_max DOUBLE PRECISION",
        "raw_s3_key TEXT NOT NULL",
        "ingested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
    ):
        assert fragment in ddl, fragment


@pytest.mark.parametrize(
    "column",
    [
        "sw_density_avg",
        "sw_sector",
        "cme_speed_max",
        "cme_width_max",
        "cme_earth_directed_speed_max",
    ],
)
def test_columns_added_for_feature_specs_exist(column: str) -> None:
    """Колонки для признаков sw_density_avg, sw_sector и cme_*_max_24h (contracts/features)."""
    assert column in TABLE.columns


def test_constraint_names_follow_naming_convention() -> None:
    assert {c.name for c in TABLE.constraints} == {
        "pk_observation_daily",
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
