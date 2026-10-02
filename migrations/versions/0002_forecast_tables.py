"""forecast tables: forecast, forecast_verification, rolling_metrics

Задача B0-06. Схема — ЭТАП1 §6 (прогнозы и контур B) с правками:
- forecast_verification: PK (forecast_id, obs_is_final) вместо (forecast_id) — по ЭТАП1 §3.6
  прогноз верифицируется дважды (оперативные и окончательные данные), обе оценки хранятся;
- forecast.feature_set_v: вместе с features_t однозначно адресует строку feature_row
  (её PK — (t, feature_set_v));
- rolling_metrics.resolution, sharpness: для панели качества (УСТ §6.2–6.3, §6.8);
- CHECK-ограничения целостности (горизонт 1..3, окно, JSON-объект, n >= 0, window_days > 0).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("horizon_days", sa.SmallInteger(), nullable=False),
        sa.Column("target_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("distribution", postgresql.JSONB(), nullable=False),
        sa.Column("point_estimate", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("calibrator_v", sa.Text(), nullable=True),
        sa.Column("features_t", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feature_set_v", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecast")),
        sa.UniqueConstraint(
            "issued_at",
            "quantity",
            "horizon_days",
            "source",
            name=op.f("uq_forecast_issued_at_quantity_horizon_days_source"),
        ),
        sa.CheckConstraint("horizon_days BETWEEN 1 AND 3", name=op.f("ck_forecast_horizon_days")),
        sa.CheckConstraint("target_to > target_from", name=op.f("ck_forecast_target_window")),
        sa.CheckConstraint(
            "jsonb_typeof(distribution) = 'object'",
            name=op.f("ck_forecast_distribution_object"),
        ),
    )
    op.create_index(
        op.f("ix_forecast_target_from_quantity"), "forecast", ["target_from", "quantity"]
    )

    op.create_table(
        "forecast_verification",
        sa.Column("forecast_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("observed_level", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Double(), nullable=True),
        sa.Column("obs_source", sa.Text(), nullable=False),
        sa.Column("obs_is_final", sa.Boolean(), nullable=False),
        sa.Column("brier", sa.Double(), nullable=True),
        sa.Column("rps", sa.Double(), nullable=True),
        sa.Column("log_score", sa.Double(), nullable=True),
        sa.Column("hit", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["forecast_id"],
            ["forecast.id"],
            name=op.f("fk_forecast_verification_forecast_id_forecast"),
        ),
        sa.PrimaryKeyConstraint(
            "forecast_id", "obs_is_final", name=op.f("pk_forecast_verification")
        ),
    )

    op.create_table(
        "rolling_metrics",
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=False),
        sa.Column("horizon_days", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("bss", sa.Double(), nullable=True),
        sa.Column("rps_mean", sa.Double(), nullable=True),
        sa.Column("reliability", sa.Double(), nullable=True),
        sa.Column("resolution", sa.Double(), nullable=True),
        sa.Column("sharpness", sa.Double(), nullable=True),
        sa.Column("tss_g1", sa.Double(), nullable=True),
        sa.Column("tss_g2", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint(
            "computed_at",
            "quantity",
            "horizon_days",
            "source",
            "window_days",
            name=op.f("pk_rolling_metrics"),
        ),
        sa.CheckConstraint(
            "horizon_days BETWEEN 1 AND 3", name=op.f("ck_rolling_metrics_horizon_days")
        ),
        sa.CheckConstraint("window_days > 0", name=op.f("ck_rolling_metrics_window_days")),
        sa.CheckConstraint("n >= 0", name=op.f("ck_rolling_metrics_n")),
    )


def downgrade() -> None:
    op.drop_table("rolling_metrics")
    op.drop_table("forecast_verification")
    op.drop_index(op.f("ix_forecast_target_from_quantity"), table_name="forecast")
    op.drop_table("forecast")
