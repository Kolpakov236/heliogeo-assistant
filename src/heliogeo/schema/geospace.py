"""Модель геокосмоса: Kp, Dst."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

_MAX_KP = 9
_MIN_KP = 0


class Geospace(BaseModel):
    """Состояние геокосмоса."""

    kp_observed: float | None = Field(default=None, description="Kp наблюдаемый (0-9)")
    dst_observed: float | None = Field(default=None, description="Dst наблюдаемый, нТл")
    current_level: Literal["quiet", "active", "storm"] = Field(
        default="quiet",
        description="Текущий уровень",
    )
    kp_fcst: float | None = Field(default=None, description="Прогноз Kp")

    @field_validator("kp_observed", "kp_fcst")
    @classmethod
    def kp_in_range(cls, v: float | None) -> float | None:
        """Kp должен быть от 0 до 9."""
        if v is not None and not _MIN_KP <= v <= _MAX_KP:
            if v > _MAX_KP:
                # Сглаживаем неточность измерений
                return _MAX_KP
            raise ValueError(f"Kp must be between 0 and 9, got {v}")
        return v
