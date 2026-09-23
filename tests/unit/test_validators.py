"""Тесты кастомных валидаторов."""

from datetime import UTC, datetime

import pytest

from heliogeo.schema.validators import (
    valid_dst,
    valid_flare_class,
    valid_kp,
    valid_utc,
)

# --- valid_utc ---


def test_valid_utc_with_z() -> None:
    """ISO 8601 с 'Z' проходит."""
    assert valid_utc("2026-05-12T00:00:00Z") == "2026-05-12T00:00:00Z"


def test_valid_utc_with_offset() -> None:
    """ISO 8601 с offset проходит."""
    assert valid_utc("2026-05-12T00:00:00+00:00") == "2026-05-12T00:00:00+00:00"


def test_valid_utc_with_datetime() -> None:
    """datetime проходит без изменений."""
    dt = datetime.now(UTC)
    assert valid_utc(dt) == dt


def test_valid_utc_invalid_string() -> None:
    """Невалидная строка падает."""
    with pytest.raises(ValueError, match="Invalid UTC time"):
        valid_utc("not-a-date")


def test_valid_utc_invalid_type() -> None:
    """Невалидный тип падает."""
    with pytest.raises(ValueError, match="must be str or datetime"):
        valid_utc(12345)  # type: ignore[arg-type]


# --- valid_kp ---


@pytest.mark.parametrize("kp", [0.0, 1.5, 5.0, 9.0])
def test_valid_kp_valid(kp: float) -> None:
    """Kp в диапазоне [0, 9] проходит."""
    assert valid_kp(kp) == kp


@pytest.mark.parametrize("kp", [-1.0, 9.5, 15.0])
def test_valid_kp_invalid(kp: float) -> None:
    """Kp вне диапазона падает."""
    with pytest.raises(ValueError, match="between 0 and 9"):
        valid_kp(kp)


def test_valid_kp_none() -> None:
    """None проходит."""
    assert valid_kp(None) is None


# --- valid_dst ---


@pytest.mark.parametrize("dst", [-500.0, -150.0, 0.0, 50.0])
def test_valid_dst_valid(dst: float) -> None:
    """Dst в диапазоне проходит."""
    assert valid_dst(dst) == dst


@pytest.mark.parametrize("dst", [-600.0, 100.0])
def test_valid_dst_invalid(dst: float) -> None:
    """Dst вне диапазона падает."""
    with pytest.raises(ValueError, match="out of range"):
        valid_dst(dst)


def test_valid_dst_none() -> None:
    """None проходит."""
    assert valid_dst(None) is None


# --- valid_flare_class ---


@pytest.mark.parametrize("cls", ["A1", "B5.5", "C1", "M1.5", "X10", "X20.5"])
def test_valid_flare_class_valid(cls: str) -> None:
    """Корректные классы проходят."""
    assert valid_flare_class(cls) == cls


@pytest.mark.parametrize("cls", ["Z1", "M", "C", "1.5", "m1", "ABC"])
def test_valid_flare_class_invalid(cls: str) -> None:
    """Некорректные классы падают."""
    with pytest.raises(ValueError, match="Invalid flare class"):
        valid_flare_class(cls)
