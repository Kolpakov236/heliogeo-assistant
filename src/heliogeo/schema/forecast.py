"""Модель прогноза."""

from pydantic import BaseModel, Field


class Forecast(BaseModel):
    """Прогностические вероятности и драйверы."""

    flare_c_prob: float | None = Field(
        default=None, ge=0, le=100, description="Вероятность C-вспышек, %"
    )
    flare_m_prob: float | None = Field(
        default=None, ge=0, le=100, description="Вероятность M-вспышек, %"
    )
    flare_x_prob: float | None = Field(
        default=None, ge=0, le=100, description="Вероятность X-вспышек, %"
    )

    proton_prob: float | None = Field(
        default=None, ge=0, le=100, description="Вероятность протонного события, %"
    )

    geomagnetic_g: str | None = Field(default=None, description="Прогноз G1-G5 (напр. G1, G2)")

    drivers: list[str] = Field(default_factory=list, description="Драйверы (Enlil, WSA и т.д.)")
