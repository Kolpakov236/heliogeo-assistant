"""Тесты ORM-моделей прогнозов без БД: структура таблиц и DDL."""

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from heliogeo.storage.models import (
    Base,
    ForecastRecord,
    ForecastVerificationRecord,
    RollingMetricsRecord,
)

MODELS = {
    "forecast": ForecastRecord,
    "forecast_verification": ForecastVerificationRecord,
    "rolling_metrics": RollingMetricsRecord,
}


def _ddl(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_models_are_registered_in_metadata() -> None:
    """Импорт heliogeo.storage.models регистрирует таблицы в Base.metadata (для Alembic)."""
    assert set(MODELS) <= set(Base.metadata.tables)
    for name, model in MODELS.items():
        assert model.__tablename__ == name


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("forecast", ["id"]),
        ("forecast_verification", ["forecast_id", "obs_is_final"]),
        (
            "rolling_metrics",
            ["computed_at", "quantity", "horizon_days", "source", "window_days"],
        ),
    ],
)
def test_primary_keys(table: str, expected: list[str]) -> None:
    """Составные ключи верификации и метрик, в порядке из схемы."""
    assert [c.name for c in Base.metadata.tables[table].primary_key.columns] == expected


def test_forecast_ddl_has_expected_types() -> None:
    """Типы PostgreSQL в DDL модели forecast."""
    ddl = _ddl("forecast")
    for fragment in (
        "id BIGSERIAL NOT NULL",
        "issued_at TIMESTAMP WITH TIME ZONE NOT NULL",
        "horizon_days SMALLINT NOT NULL",
        "distribution JSONB NOT NULL",
        "point_estimate TEXT,",
    ):
        assert fragment in ddl, fragment


def test_verification_and_metrics_ddl_use_double_precision() -> None:
    """Дробные метрики — DOUBLE PRECISION; у verified_at есть серверный now()."""
    verification = _ddl("forecast_verification")
    assert "brier DOUBLE PRECISION" in verification
    assert "verified_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in verification
    assert "forecast_id BIGINT NOT NULL" in verification

    metrics = _ddl("rolling_metrics")
    for column in ("bss", "rps_mean", "reliability", "resolution", "sharpness", "tss_g1", "tss_g2"):
        assert f"{column} DOUBLE PRECISION" in metrics, column
    assert "n INTEGER NOT NULL" in metrics


def test_constraint_names_follow_naming_convention() -> None:
    """Имена ограничений детерминированы соглашением (на них опирается миграция 0002)."""
    names = {
        constraint.name
        for table in (
            Base.metadata.tables["forecast"],
            Base.metadata.tables["forecast_verification"],
            Base.metadata.tables["rolling_metrics"],
        )
        for constraint in table.constraints
    }
    assert names == {
        "pk_forecast",
        "uq_forecast_issued_at_quantity_horizon_days_source",
        "ck_forecast_horizon_days",
        "ck_forecast_target_window",
        "ck_forecast_distribution_object",
        "pk_forecast_verification",
        "fk_forecast_verification_forecast_id_forecast",
        "pk_rolling_metrics",
        "ck_rolling_metrics_horizon_days",
        "ck_rolling_metrics_window_days",
        "ck_rolling_metrics_n",
    }
