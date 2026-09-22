"""Тесты логирования."""

import logging

from heliogeo.core.config import get_settings
from heliogeo.core.logging import get_logger, setup_logging


def test_setup_logging_sets_level() -> None:
    """setup_logging должен устанавливать уровень корневого логгера."""
    setup_logging(level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG

    setup_logging(level="INFO")
    assert logging.getLogger().level == logging.INFO


def test_get_logger_returns_logger() -> None:
    """get_logger должен возвращать logging.Logger."""
    log = get_logger("heliogeo.test")
    assert isinstance(log, logging.Logger)
    assert log.name == "heliogeo.test"


def test_settings_have_defaults() -> None:
    """Settings должны иметь разумные значения по умолчанию."""
    settings = get_settings()
    assert settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert settings.tz == "UTC"


def test_settings_singleton() -> None:
    """get_settings должен возвращать один и тот же объект."""
    assert get_settings() is get_settings()
