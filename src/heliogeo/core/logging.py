"""Настройка логирования для всего проекта."""

import logging
import logging.config
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "logging.yaml"


def setup_logging(
    level: str = "INFO",
    config_path: Path | None = None,
) -> None:
    """Настроить корневой логгер.

    Args:
        level: уровень логирования (DEBUG, INFO, WARNING, ERROR).
        config_path: путь к logging.yaml. По умолчанию configs/logging.yaml.
    """
    path = config_path or _DEFAULT_CONFIG

    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    logging.getLogger().setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Получить логгер по имени модуля."""
    return logging.getLogger(name)
