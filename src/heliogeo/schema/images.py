"""Модель изображений (URL на DAV)."""

from pydantic import BaseModel, Field


class Images(BaseModel):
    """URL изображений на DAV-сервере."""

    magnetogram: str | None = Field(default=None)
    ha: str | None = Field(default=None, description="H-alpha")
    euv193: str | None = Field(default=None)
    lasco_c2: str | None = Field(default=None)
    lasco_c3: str | None = Field(default=None)
    xray_plot: str | None = Field(default=None)
