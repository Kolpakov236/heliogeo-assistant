"""Пакеты-заготовки целевой структуры: обычные пакеты с описанием назначения."""

import importlib
from pathlib import Path

import pytest

SKELETON_PACKAGES = [
    "mirror",
    "facts",
    "features",
    "forecast",
    "planner",
    "generation",
    "validation",
    "publishing",
    "eval",
]

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "heliogeo"


@pytest.mark.parametrize("name", SKELETON_PACKAGES)
def test_package_is_importable(name: str) -> None:
    module = importlib.import_module(f"heliogeo.{name}")
    assert module.__name__ == f"heliogeo.{name}"


@pytest.mark.parametrize("name", SKELETON_PACKAGES)
def test_package_has_init_file(name: str) -> None:
    """Обычный пакет с ``__init__.py``, а не namespace-пакет: от этого зависит import-linter."""
    assert (SRC_DIR / name / "__init__.py").is_file()
    module = importlib.import_module(f"heliogeo.{name}")
    assert module.__file__ is not None


@pytest.mark.parametrize("name", SKELETON_PACKAGES)
def test_package_describes_its_purpose(name: str) -> None:
    module = importlib.import_module(f"heliogeo.{name}")
    assert module.__doc__ is not None
    assert len(module.__doc__.strip()) >= 30, "docstring должен объяснять назначение пакета"
