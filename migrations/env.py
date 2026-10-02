"""Окружение Alembic: URL базы и метаданные моделей берутся из приложения."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from heliogeo.core.config import get_settings
from heliogeo.storage.db import normalize_url
from heliogeo.storage.models import Base

config = context.config

# Из тестов (cfg.attributes["configure_logger"] = False) настройка логов не нужна:
# fileConfig сбрасывает обработчики корневого логгера и ломает перехват логов в pytest.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    """URL БД: явный sqlalchemy.url (тесты, CLI) или настройки приложения (.env)."""
    return normalize_url(config.get_main_option("sqlalchemy.url") or get_settings().database_url)


def run_migrations_offline() -> None:
    """Режим без подключения: SQL выводится в stdout (alembic upgrade head --sql)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Обычный режим: каждая миграция выполняется в отдельной транзакции."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
