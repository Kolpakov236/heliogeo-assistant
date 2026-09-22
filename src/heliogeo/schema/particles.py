from pydantic import BaseModel, Field


class EnergeticParticles(BaseModel):
    """Энергичные частицы: электроны и протоны."""

    e_2mev: float | None = Field(default=None, description=">2 MeV электроны, pfu")
    p_10mev_peak: float | None = Field(default=None, description=">10 MeV протоны, peak, pfu")
    p_10mev_background: float | None = Field(
        default=None, description=">10 MeV протоны, background, pfu"
    )
    p_100mev_peak: float | None = Field(default=None, description=">100 MeV протоны, peak, pfu")
    sep_flag: bool = Field(default=False, description="Solar Energetic Particle event flag")
