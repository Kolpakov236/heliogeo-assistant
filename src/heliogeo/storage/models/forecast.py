"""Модели таблиц прогнозов: forecast, forecast_verification, rolling_metrics.

Схема — ЭТАП1 §6 (контур B: прогнозы, их верификация, скользящие метрики). Отличия от
ЭТАП1 §6 помечены «(!)»; подробности и обоснование — в docs/database.md.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from heliogeo.storage.models.base import Base


class ForecastRecord(Base):
    """Прогноз одного источника на одно целевое окно (одна строка = один прогноз)."""

    __tablename__ = "forecast"
    __table_args__ = (
        UniqueConstraint("issued_at", "quantity", "horizon_days", "source"),
        CheckConstraint("horizon_days BETWEEN 1 AND 3", name="horizon_days"),
        CheckConstraint("target_to > target_from", name="target_window"),
        CheckConstraint("jsonb_typeof(distribution) = 'object'", name="distribution_object"),
        Index("ix_forecast_target_from_quantity", "target_from", "quantity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 'geomag_level' | 'flare_m' | 'flare_x'
    quantity: Mapped[str] = mapped_column(Text)
    # 1 | 2 | 3
    horizon_days: Mapped[int] = mapped_column(SmallInteger)
    target_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # ml | persistence | recurrence | climatology | swpc
    source: Mapped[str] = mapped_column(Text)
    # {"quiet": 0.4, "G1": 0.2, ...}
    distribution: Mapped[dict[str, float]] = mapped_column(JSONB)
    point_estimate: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    calibrator_v: Mapped[str | None] = mapped_column(Text)
    # Строка признаков (feature_row): момент и (!) версия набора — вместе они адресуют
    # её однозначно, т.к. PK feature_row = (t, feature_set_v). Жёсткого FK нет.
    features_t: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feature_set_v: Mapped[str | None] = mapped_column(Text)


class ForecastVerificationRecord(Base):
    """Результат сверки прогноза с наблюдением (контур B).

    (!) PK = (forecast_id, obs_is_final): по ЭТАП1 §3.6 прогноз верифицируется дважды —
    по оперативным и по окончательным данным, обе оценки хранятся.
    """

    __tablename__ = "forecast_verification"

    forecast_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("forecast.id"), primary_key=True
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    observed_level: Mapped[str] = mapped_column(Text)
    observed_value: Mapped[float | None] = mapped_column(Double)
    # По какому источнику проверяли
    obs_source: Mapped[str] = mapped_column(Text)
    # Окончательные данные (True) или оперативные (False)
    obs_is_final: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    brier: Mapped[float | None] = mapped_column(Double)
    rps: Mapped[float | None] = mapped_column(Double)
    log_score: Mapped[float | None] = mapped_column(Double)
    hit: Mapped[bool | None] = mapped_column(Boolean)


class RollingMetricsRecord(Base):
    """Метрики качества прогнозов на скользящем окне (пересчитываются ежесуточно)."""

    __tablename__ = "rolling_metrics"
    __table_args__ = (
        CheckConstraint("horizon_days BETWEEN 1 AND 3", name="horizon_days"),
        CheckConstraint("window_days > 0", name="window_days"),
        CheckConstraint("n >= 0", name="n"),
    )

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    quantity: Mapped[str] = mapped_column(Text, primary_key=True)
    horizon_days: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    window_days: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    n: Mapped[int] = mapped_column(Integer)
    bss: Mapped[float | None] = mapped_column(Double)
    rps_mean: Mapped[float | None] = mapped_column(Double)
    # Отклонение от диагонали диаграммы надёжности
    reliability: Mapped[float | None] = mapped_column(Double)
    # (!) Разрешение и резкость (УСТ §6.2–6.3) — для панели качества (УСТ §6.8)
    resolution: Mapped[float | None] = mapped_column(Double)
    sharpness: Mapped[float | None] = mapped_column(Double)
    tss_g1: Mapped[float | None] = mapped_column(Double)
    tss_g2: Mapped[float | None] = mapped_column(Double)
