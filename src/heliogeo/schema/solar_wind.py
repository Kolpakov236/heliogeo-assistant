"""Модель солнечного ветра."""

from typing import Literal

from pydantic import BaseModel, Field


class SolarWind(BaseModel):
    """Параметры солнечного ветра."""

    speed_min: float | None = Field(default=None, description="Минимальная скорость, км/с")
    speed_avg: float | None = Field(default=None, description="Средняя скорость, км/с")
    speed_max: float | None = Field(default=None, description="Максимальная скорость, км/с")

    density: float | None = Field(default=None, description="Плотность, частиц/см³")

    bt: float | None = Field(default=None, description="Полная индукция ММП, нТл")
    bz_min: float | None = Field(default=None, description="Bz min, нТл")
    bz_max: float | None = Field(default=None, description="Bz max, нТл")
    phi: float | None = Field(default=None, description="Фи-угол ММП, градусы")

    temperature: float | None = Field(default=None, description="Температура, K")

    source: Literal["CH HSS", "CME", "unknown"] = Field(
        default="unknown",
        description="Источник: CH HSS / CME / unknown",
    )
