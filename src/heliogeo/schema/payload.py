"""Корневая модель Unified JSON."""

from datetime import datetime

from pydantic import BaseModel, Field

from heliogeo.schema.forecast import Forecast
from heliogeo.schema.geospace import Geospace
from heliogeo.schema.images import Images
from heliogeo.schema.particles import EnergeticParticles
from heliogeo.schema.solar_activity import SolarActivity
from heliogeo.schema.solar_wind import SolarWind


class Metadata(BaseModel):
    """Метаданные пейлоада."""

    issued_utc: datetime = Field(..., description="Время выпуска (UTC)")
    valid_until_utc: datetime = Field(..., description="Действительно до (UTC)")
    generator_version: str = Field(default="1.0", description="Версия генератора")
    source_products: list[str] = Field(..., description="Источники")


class UnifiedPayload(BaseModel):
    """Единый JSON-пейлоад для LLM."""

    metadata: Metadata
    solar_activity: SolarActivity = Field(default_factory=SolarActivity)
    energetic_particles: EnergeticParticles = Field(default_factory=EnergeticParticles)
    solar_wind: SolarWind = Field(default_factory=SolarWind)
    geospace: Geospace = Field(default_factory=Geospace)
    forecast: Forecast = Field(default_factory=Forecast)
    images: Images = Field(default_factory=Images)
    technical_issues: str | None = Field(
        default=None, description="Технические проблемы (AFTN/AMHS и т.д.)"
    )
