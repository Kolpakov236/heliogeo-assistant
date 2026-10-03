"""observation_daily: суточные наблюдения (часть задачи B0-05)

Таблица observation_daily по ЭТАП1 §6 с правками:
- колонки sw_density_avg, sw_sector и кинематика выбросов (cme_speed_max, cme_width_max,
  cme_earth_directed_speed_max): без них не посчитать признаки sw_density_avg, sw_sector и
  cme_*_max_24h из contracts/features/specs.yaml;
- CHECK-ограничения: kp_values не длиннее 8 (защита от «двойного Kp»), счётчики не
  отрицательны, выбросы «к Земле» и «направление неизвестно» не больше общего числа, JSONB —
  объекты.

Таблица feature_row, отпечаток прибора (fingerprint_key) и остальное в B0-05 сюда не входят.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observation_daily",
        sa.Column("d", sa.Date(), nullable=False),
        sa.Column("kp_max", sa.Double(), nullable=True),
        sa.Column("kp_values", postgresql.ARRAY(sa.Double()), nullable=True),
        sa.Column("kp_source", sa.Text(), nullable=True),
        sa.Column("dst_min", sa.Double(), nullable=True),
        sa.Column("dst_source", sa.Text(), nullable=True),
        sa.Column("sw_speed_min", sa.Double(), nullable=True),
        sa.Column("sw_speed_avg", sa.Double(), nullable=True),
        sa.Column("sw_speed_max", sa.Double(), nullable=True),
        sa.Column("sw_bt", sa.Double(), nullable=True),
        sa.Column("sw_bz_min", sa.Double(), nullable=True),
        sa.Column("sw_density_avg", sa.Double(), nullable=True),
        sa.Column("sw_sector", sa.Text(), nullable=True),
        sa.Column("sw_source", sa.Text(), nullable=True),
        sa.Column("flare_max_class", sa.Text(), nullable=True),
        sa.Column("flare_counts", postgresql.JSONB(), nullable=True),
        sa.Column("ar_count", sa.Integer(), nullable=True),
        sa.Column("ar_total_area", sa.Integer(), nullable=True),
        sa.Column("ar_has_delta", sa.Boolean(), nullable=True),
        sa.Column("ar_summary", postgresql.JSONB(), nullable=True),
        sa.Column("cme_count", sa.Integer(), nullable=True),
        sa.Column("cme_earth_directed", sa.Integer(), nullable=True),
        sa.Column("cme_direction_unknown", sa.Integer(), nullable=True),
        sa.Column("cme_speed_max", sa.Double(), nullable=True),
        sa.Column("cme_width_max", sa.Double(), nullable=True),
        sa.Column("cme_earth_directed_speed_max", sa.Double(), nullable=True),
        sa.Column("qc", postgresql.JSONB(), nullable=True),
        sa.Column("raw_s3_key", sa.Text(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("d", name=op.f("pk_observation_daily")),
        sa.CheckConstraint(
            "cardinality(kp_values) <= 8", name=op.f("ck_observation_daily_kp_values_len")
        ),
        sa.CheckConstraint("ar_count >= 0", name=op.f("ck_observation_daily_ar_count")),
        sa.CheckConstraint("ar_total_area >= 0", name=op.f("ck_observation_daily_ar_total_area")),
        sa.CheckConstraint("cme_count >= 0", name=op.f("ck_observation_daily_cme_count")),
        sa.CheckConstraint(
            "cme_earth_directed >= 0", name=op.f("ck_observation_daily_cme_earth_directed")
        ),
        sa.CheckConstraint(
            "cme_direction_unknown >= 0", name=op.f("ck_observation_daily_cme_direction_unknown")
        ),
        sa.CheckConstraint(
            "cme_earth_directed + cme_direction_unknown <= cme_count",
            name=op.f("ck_observation_daily_cme_split"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(flare_counts) = 'object'",
            name=op.f("ck_observation_daily_flare_counts_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ar_summary) = 'object'",
            name=op.f("ck_observation_daily_ar_summary_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(qc) = 'object'", name=op.f("ck_observation_daily_qc_object")
        ),
    )


def downgrade() -> None:
    op.drop_table("observation_daily")
