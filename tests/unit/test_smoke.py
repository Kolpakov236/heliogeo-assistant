"""Smoke-тесты: проверяют, что пакет импортируется и версия корректна."""

import heliogeo


def test_version_exists() -> None:
    """Версия должна быть строкой."""
    assert isinstance(heliogeo.__version__, str)


def test_version_format() -> None:
    """Версия должна быть в формате X.Y.Z."""
    parts = heliogeo.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_cli_importable() -> None:
    """CLI должен импортироваться без ошибок."""
    from heliogeo.cli.main import app

    assert app is not None
