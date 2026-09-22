from datetime import datetime

from pydantic import BaseModel, Field


class ActiveRegion(BaseModel):
    """Активный регион на Солнце."""

    noaa_num: int = Field(..., description="Номер NOAA AR")
    location: str = Field(..., description="Гелиографические координаты, напр. N15W30")
    mcintosh: str | None = Field(default=None, description="Класс McIntosh")
    magnetic_class: str | None = Field(
        default=None, description="Магнитный класс (alpha, beta, beta-gamma и т.д.)"
    )
    area: int | None = Field(default=None, description="Площадь в миллионных долях полусферы")
    spots: int | None = Field(default=None, description="Количество пятен")
    growth: str | None = Field(default=None, description="Тренд роста (growing, stable, decaying)")


class Flare(BaseModel):
    """Солнечная вспышка."""

    time: datetime = Field(..., description="Время вспышки (UTC)")
    class_: str = Field(..., alias="class", description="Класс вспышки (A/B/C/M/X + число)")
    ar: int | None = Field(default=None, description="Номер NOAA AR, если известен")
    radio_signatures: list[str] = Field(
        default_factory=list, description="Радиосигнатуры (Type II, Type IV и т.д.)"
    )

    model_config = {"populate_by_name": True}


class CME(BaseModel):
    """Корональный выброс массы."""

    time: datetime = Field(..., description="Время выброса (UTC)")
    speed: float = Field(..., description="Скорость, км/с")
    width: float = Field(..., description="Угловая ширина, градусы")
    earth_directed: bool = Field(..., description="Направлен ли к Земле")


class SolarActivity(BaseModel):
    """Секция солнечной активности в Unified JSON."""

    active_regions: list[ActiveRegion] = Field(default_factory=list)
    flares_last_24h: list[Flare] = Field(default_factory=list)
    cme_list: list[CME] = Field(default_factory=list)
    f107: float | None = Field(default=None, description="F10.7 см поток")
    ssn: int | None = Field(default=None, description="Sunspot Number")
