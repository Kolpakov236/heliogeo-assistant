"""Интеграционные тесты таблиц прогнозов (миграция 0002, задача B0-06).

Схема — ЭТАП1 §6 с правками, перечисленными в migrations/versions/0002_forecast_tables.py.
Данные берутся из моков (tests/mocks/forecast.py).
"""

from datetime import timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from heliogeo.storage.models import (
    ForecastRecord,
    ForecastVerificationRecord,
    RollingMetricsRecord,
)
from tests.integration.helpers import make_alembic_config, tables
from tests.mocks.forecast import (
    ISSUED_AT,
    SOURCES,
    make_distribution,
    make_forecast,
    make_issue_forecasts,
    make_rolling_metrics,
    make_verification,
)

pytestmark = pytest.mark.db

FORECAST_TABLES = {"forecast", "forecast_verification", "rolling_metrics"}

TIMESTAMPTZ = "timestamp with time zone"
DOUBLE = "double precision"

# (колонка, тип PostgreSQL, допускает NULL) в порядке ЭТАП1 §6, с правками миграции 0002
EXPECTED_COLUMNS: dict[str, list[tuple[str, str, bool]]] = {
    "forecast": [
        ("id", "bigint", False),
        ("issued_at", TIMESTAMPTZ, False),
        ("quantity", "text", False),
        ("horizon_days", "smallint", False),
        ("target_from", TIMESTAMPTZ, False),
        ("target_to", TIMESTAMPTZ, False),
        ("source", "text", False),
        ("distribution", "jsonb", False),
        ("point_estimate", "text", True),
        ("model_version", "text", True),
        ("calibrator_v", "text", True),
        ("features_t", TIMESTAMPTZ, True),
        ("feature_set_v", "text", True),
    ],
    "forecast_verification": [
        ("forecast_id", "bigint", False),
        ("verified_at", TIMESTAMPTZ, False),
        ("observed_level", "text", False),
        ("observed_value", DOUBLE, True),
        ("obs_source", "text", False),
        ("obs_is_final", "boolean", False),
        ("brier", DOUBLE, True),
        ("rps", DOUBLE, True),
        ("log_score", DOUBLE, True),
        ("hit", "boolean", True),
    ],
    "rolling_metrics": [
        ("computed_at", TIMESTAMPTZ, False),
        ("quantity", "text", False),
        ("horizon_days", "smallint", False),
        ("source", "text", False),
        ("window_days", "smallint", False),
        ("n", "integer", False),
        ("bss", DOUBLE, True),
        ("rps_mean", DOUBLE, True),
        ("reliability", DOUBLE, True),
        ("resolution", DOUBLE, True),
        ("sharpness", DOUBLE, True),
        ("tss_g1", DOUBLE, True),
        ("tss_g2", DOUBLE, True),
    ],
}


# --- схема ---


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_columns_match_spec(migrated_engine: Engine, table: str) -> None:
    """Имена, порядок, типы и NULL-ность колонок соответствуют схеме."""
    query = text(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :table ORDER BY ordinal_position"
    )
    with migrated_engine.connect() as connection:
        rows = connection.execute(query, {"table": table}).all()
    assert [(name, dtype, nullable == "YES") for name, dtype, nullable in rows] == (
        EXPECTED_COLUMNS[table]
    )


def test_server_defaults(migrated_engine: Engine) -> None:
    """Значения по умолчанию на стороне БД: автоинкремент id и now() у verified_at."""
    query = text(
        "SELECT table_name || '.' || column_name, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_default IS NOT NULL "
        "AND table_name IN ('forecast', 'forecast_verification', 'rolling_metrics')"
    )
    with migrated_engine.connect() as connection:
        defaults = dict(connection.execute(query).all())
    assert set(defaults) == {"forecast.id", "forecast_verification.verified_at"}
    assert str(defaults["forecast.id"]).startswith("nextval(")
    assert defaults["forecast_verification.verified_at"] == "now()"


def test_primary_keys(migrated_engine: Engine) -> None:
    """Первичные ключи; у верификации — составной (две оценки на прогноз)."""
    inspector = inspect(migrated_engine)
    assert inspector.get_pk_constraint("forecast")["constrained_columns"] == ["id"]
    assert inspector.get_pk_constraint("forecast_verification")["constrained_columns"] == [
        "forecast_id",
        "obs_is_final",
    ]
    assert inspector.get_pk_constraint("rolling_metrics")["constrained_columns"] == [
        "computed_at",
        "quantity",
        "horizon_days",
        "source",
        "window_days",
    ]


def test_unique_index_and_foreign_key(migrated_engine: Engine) -> None:
    """UNIQUE по ключу выпуска, индекс по целевому окну и FK верификации на прогноз."""
    inspector = inspect(migrated_engine)

    uniques = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints("forecast")}
    assert uniques == {
        "uq_forecast_issued_at_quantity_horizon_days_source": [
            "issued_at",
            "quantity",
            "horizon_days",
            "source",
        ]
    }

    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("forecast")}
    assert indexes["ix_forecast_target_from_quantity"] == ["target_from", "quantity"]

    foreign_keys = inspector.get_foreign_keys("forecast_verification")
    assert [
        (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
        for fk in foreign_keys
    ] == [(["forecast_id"], "forecast", ["id"])]


def test_check_constraints_exist(migrated_engine: Engine) -> None:
    """Ограничения целостности создаются с ожидаемыми именами."""
    inspector = inspect(migrated_engine)
    assert {c["name"] for c in inspector.get_check_constraints("forecast")} == {
        "ck_forecast_horizon_days",
        "ck_forecast_target_window",
        "ck_forecast_distribution_object",
    }
    assert {c["name"] for c in inspector.get_check_constraints("rolling_metrics")} == {
        "ck_rolling_metrics_horizon_days",
        "ck_rolling_metrics_window_days",
        "ck_rolling_metrics_n",
    }


# --- forecast ---


def test_forecast_roundtrip(session: Session) -> None:
    """Прогноз сохраняется и читается без потерь (включая JSONB и tz-aware время)."""
    forecast = make_forecast(source="ml", model_version="m1-2026.09", calibrator_v="iso-1")
    session.add(forecast)
    session.flush()
    assert forecast.id is not None

    session.expire(forecast)
    loaded = session.get(ForecastRecord, forecast.id)
    assert loaded is not None
    assert loaded.distribution == make_distribution()
    assert loaded.issued_at == ISSUED_AT
    assert loaded.target_to - loaded.target_from == timedelta(days=1)
    assert (loaded.point_estimate, loaded.model_version, loaded.calibrator_v) == (
        "quiet",
        "m1-2026.09",
        "iso-1",
    )
    assert (loaded.features_t, loaded.feature_set_v) == (ISSUED_AT, "1")


def test_issue_key_is_unique(session: Session) -> None:
    """(issued_at, quantity, horizon_days, source) не повторяется."""
    session.add(make_forecast())
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_forecast(model_version="другая версия"))
        session.flush()


def test_whole_issue_is_stored_and_found_by_target_window(session: Session) -> None:
    """Выпуск целиком (5 источников × 3 горизонта) помещается и ищется по целевому окну."""
    forecasts = make_issue_forecasts()
    session.add_all(forecasts)
    session.flush()
    assert len(forecasts) == 15

    rows = session.scalars(
        select(ForecastRecord).where(
            ForecastRecord.quantity == "geomag_level",
            ForecastRecord.target_from == ISSUED_AT + timedelta(days=1),
        )
    ).all()
    assert {r.source for r in rows} == set(SOURCES)
    assert {r.horizon_days for r in rows} == {2}


@pytest.mark.parametrize(
    "overrides",
    [
        {"horizon_days": 0},
        {"horizon_days": 4},
        {"target_to": ISSUED_AT},  # окно нулевой длины
        {"distribution": ["quiet", 1.0], "point_estimate": None},  # не JSON-объект
    ],
    ids=["horizon-0", "horizon-4", "empty-window", "distribution-not-object"],
)
def test_forecast_check_constraints_reject_invalid_rows(
    session: Session, overrides: dict[str, Any]
) -> None:
    """CHECK-ограничения отклоняют некорректные прогнозы."""
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_forecast(**overrides))
        session.flush()


# --- forecast_verification ---


def test_verification_requires_existing_forecast(session: Session) -> None:
    """Верификация несуществующего прогноза нарушает внешний ключ."""
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_verification(make_forecast(), forecast_id=-1))
        session.flush()


def test_forecast_is_verified_twice_operational_and_final(session: Session) -> None:
    """Обе оценки хранятся: по оперативным (False) и по окончательным (True) данным."""
    forecast = make_forecast(distribution=make_distribution("G1"))
    session.add(forecast)
    session.flush()
    session.add_all(
        [
            make_verification(forecast, "G1", obs_is_final=False),
            make_verification(forecast, "G2", obs_is_final=True),
        ]
    )
    session.flush()

    rows = session.scalars(
        select(ForecastVerificationRecord)
        .where(ForecastVerificationRecord.forecast_id == forecast.id)
        .order_by(ForecastVerificationRecord.obs_is_final)
    ).all()
    assert [(r.obs_is_final, r.observed_level, r.hit) for r in rows] == [
        (False, "G1", True),
        (True, "G2", False),
    ]


def test_second_verification_of_same_kind_is_rejected(session: Session) -> None:
    """Повторная оценка того же рода (оба раза окончательная) отклоняется."""
    forecast = make_forecast()
    session.add(forecast)
    session.flush()
    session.add(make_verification(forecast, obs_is_final=True))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_verification(forecast, "G1", obs_is_final=True))
        session.flush()


def test_verified_at_is_filled_by_database(session: Session) -> None:
    """verified_at заполняется БД (now()), если не задан."""
    forecast = make_forecast()
    session.add(forecast)
    session.flush()
    verification = make_verification(forecast)
    session.add(verification)
    session.flush()
    session.refresh(verification)
    assert verification.verified_at is not None
    assert abs(session.scalar(select(func.now())) - verification.verified_at) < timedelta(minutes=1)


# --- rolling_metrics ---


def test_rolling_metrics_roundtrip(session: Session) -> None:
    """Метрики сохраняются и читаются; необязательные поля могут быть NULL."""
    session.add(make_rolling_metrics())
    session.flush()
    loaded = session.scalars(select(RollingMetricsRecord)).one()
    assert (loaded.n, loaded.bss, loaded.resolution, loaded.sharpness) == (90, 0.094, 0.0071, 0.24)
    assert loaded.tss_g2 is None


def test_rolling_metrics_primary_key_is_unique(session: Session) -> None:
    """Одна строка на (computed_at, quantity, horizon_days, source, window_days)."""
    session.add(make_rolling_metrics())
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_rolling_metrics(bss=0.5))
        session.flush()


def test_rolling_metrics_allows_other_source_window_and_horizon(session: Session) -> None:
    """Отличие в источнике, окне или горизонте — другая строка."""
    session.add_all(
        [
            make_rolling_metrics(),
            make_rolling_metrics(source="swpc"),
            make_rolling_metrics(window_days=180),
            make_rolling_metrics(horizon_days=2),
        ]
    )
    session.flush()
    assert session.scalar(select(func.count()).select_from(RollingMetricsRecord)) == 4


@pytest.mark.parametrize(
    "overrides",
    [{"horizon_days": 0}, {"horizon_days": 4}, {"window_days": 0}, {"n": -1}],
    ids=["horizon-0", "horizon-4", "window-0", "n-negative"],
)
def test_rolling_metrics_check_constraints_reject_invalid_rows(
    session: Session, overrides: dict[str, Any]
) -> None:
    """CHECK-ограничения отклоняют некорректные метрики."""
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(make_rolling_metrics(**overrides))
        session.flush()


# --- откат миграции ---


def test_downgrade_removes_exactly_forecast_tables(empty_db_url: str) -> None:
    """Откат 0002 убирает ровно три таблицы, повторный накат возвращает их."""
    cfg = make_alembic_config(empty_db_url)

    command.upgrade(cfg, "0002")
    with_tables = tables(empty_db_url)
    command.downgrade(cfg, "0001")
    without_tables = tables(empty_db_url)

    assert with_tables - without_tables == FORECAST_TABLES
    assert without_tables == {"alembic_version"}

    command.upgrade(cfg, "0002")
    assert tables(empty_db_url) >= FORECAST_TABLES
