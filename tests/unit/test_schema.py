"""Тесты Pydantic-схем Unified JSON."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from heliogeo.schema import (
    ActiveRegion,
    EnergeticParticles,
    Flare,
    Geospace,
    Metadata,
    SolarWind,
    UnifiedPayload,
)


def test_metadata_valid() -> None:
    """Metadata создаётся с корректными полями."""
    now = datetime.now(UTC)
    meta = Metadata(
        issued_utc=now,
        valid_until_utc=now,
        generator_version="1.0",
    )
    assert meta.generator_version == "1.0"


def test_active_region_minimal() -> None:
    """ActiveRegion достаточно noaa_num + location."""
    ar = ActiveRegion(noaa_num=12345, location="N15W30")
    assert ar.noaa_num == 12345


def test_flare_with_alias() -> None:
    """Flare принимает 'class' как алиас для class_."""
    now = datetime.now(UTC)
    flare = Flare(time=now, **{"class": "M1.5"})
    assert flare.class_ == "M1.5"


def test_geospace_kp_validation() -> None:
    """Kp должен быть от 0 до 9."""
    with pytest.raises(ValidationError):
        Geospace(kp_observed=12.0)


def test_solar_wind_default_source() -> None:
    """По умолчанию source = 'unknown'."""
    sw = SolarWind()
    assert sw.source == "unknown"


def test_energetic_particles_defaults() -> None:
    """EnergeticParticles по умолчанию sep_flag=False."""
    ep = EnergeticParticles()
    assert ep.sep_flag is False


def test_unified_payload_minimal() -> None:
    """UnifiedPayload создаётся с одними metadata."""
    now = datetime.now(UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    assert payload.metadata.generator_version == "1.0"
    assert payload.solar_activity.active_regions == []


def test_unified_payload_json_roundtrip() -> None:
    """Сериализация в JSON и обратно."""
    now = datetime.now(UTC)
    payload = UnifiedPayload(
        metadata=Metadata(issued_utc=now, valid_until_utc=now),
    )
    json_str = payload.model_dump_json()
    restored = UnifiedPayload.model_validate_json(json_str)
    assert restored.metadata.issued_utc == payload.metadata.issued_utc


def test_unified_payload_json_schema() -> None:
    """JSON Schema генерируется без ошибок."""
    schema = UnifiedPayload.model_json_schema()
    assert "properties" in schema
    assert "metadata" in schema["properties"]
