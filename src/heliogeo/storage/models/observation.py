"""Модель таблицы observation_daily: суточные наблюдения (задача B0-05, часть).

Схема — ЭТАП1 §6. Отличия от ЭТАП1 §6 помечены «(!)»; подробности — в docs/database.md.
Таблица признаков feature_row и отпечаток прибора в эту часть не входят.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Double, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from heliogeo.storage.models.base import Base


class ObservationDailyRecord(Base):
    """Наблюдения за одни сутки: одна строка — одна дата. Значение NULL — «нет данных»."""

    __tablename__ = "observation_daily"
    __table_args__ = (
        # (!) Страховка от «ловушки двойного Kp»: берём 8 значений из geomag.kp_obs, а не 24
        # из solar_wind.kp_obs (РУК §2.4)
        CheckConstraint("cardinality(kp_values) <= 8", name="kp_values_len"),
        CheckConstraint("ar_count >= 0", name="ar_count"),
        CheckConstraint("ar_total_area >= 0", name="ar_total_area"),
        CheckConstraint("cme_count >= 0", name="cme_count"),
        CheckConstraint("cme_earth_directed >= 0", name="cme_earth_directed"),
        CheckConstraint("cme_direction_unknown >= 0", name="cme_direction_unknown"),
        # Каждый выброс — к Земле, мимо или «направление неизвестно»
        CheckConstraint(
            "cme_earth_directed + cme_direction_unknown <= cme_count", name="cme_split"
        ),
        CheckConstraint("jsonb_typeof(flare_counts) = 'object'", name="flare_counts_object"),
        CheckConstraint("jsonb_typeof(ar_summary) = 'object'", name="ar_summary_object"),
        CheckConstraint("jsonb_typeof(qc) = 'object'", name="qc_object"),
    )

    d: Mapped[date] = mapped_column(Date, primary_key=True)

    # Геомагнитная обстановка
    kp_max: Mapped[float | None] = mapped_column(Double)
    kp_values: Mapped[list[float] | None] = mapped_column(ARRAY(Double))  # 8 трёхчасовых
    kp_source: Mapped[str | None] = mapped_column(Text)  # 'gfz' — канонический
    dst_min: Mapped[float | None] = mapped_column(Double)
    dst_source: Mapped[str | None] = mapped_column(Text)  # final|provisional|realtime

    # Солнечный ветер
    sw_speed_min: Mapped[float | None] = mapped_column(Double)
    sw_speed_avg: Mapped[float | None] = mapped_column(Double)
    sw_speed_max: Mapped[float | None] = mapped_column(Double)
    sw_bt: Mapped[float | None] = mapped_column(Double)
    sw_bz_min: Mapped[float | None] = mapped_column(Double)
    # (!) Средняя плотность (источник: solar_wind.density.avg; «latest» бывает пуст), частиц/см³
    sw_density_avg: Mapped[float | None] = mapped_column(Double)
    # (!) Сектор межпланетного магнитного поля: toward|away (источник: solar_wind.imf.sector)
    sw_sector: Mapped[str | None] = mapped_column(Text)
    sw_source: Mapped[str | None] = mapped_column(Text)  # rtsw|omni_hro_1min|omni2_hourly

    # Вспышки
    flare_max_class: Mapped[str | None] = mapped_column(Text)
    flare_counts: Mapped[dict[str, int] | None] = mapped_column(JSONB)  # {"B":5,"C":3,"M":0,"X":0}

    # Активные области
    ar_count: Mapped[int | None] = mapped_column(Integer)
    ar_total_area: Mapped[int | None] = mapped_column(Integer)
    ar_has_delta: Mapped[bool | None] = mapped_column(Boolean)
    # Сводка по классам McIntosh и по росту (growth.status: new|continuing); структуру задаёт B1-14
    ar_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Корональные выбросы массы
    cme_count: Mapped[int | None] = mapped_column(Integer)
    cme_earth_directed: Mapped[int | None] = mapped_column(Integer)
    cme_direction_unknown: Mapped[int | None] = mapped_column(Integer)  # earth_directed = null
    # (!) Кинематика: максимум за сутки; NULL, если выбросов (к Земле) не было
    cme_speed_max: Mapped[float | None] = mapped_column(Double)  # км/с
    cme_width_max: Mapped[float | None] = mapped_column(Double)  # градусы
    cme_earth_directed_speed_max: Mapped[float | None] = mapped_column(Double)  # км/с

    qc: Mapped[dict[str, str] | None] = mapped_column(JSONB)  # статусы по группам полей
    # Сырьё сохраняется раньше разбора (РУК §4.1), поэтому ключ обязателен
    raw_s3_key: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
