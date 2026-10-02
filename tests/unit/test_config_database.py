"""Тесты сборки DSN в настройках (Settings.database_url из POSTGRES_*)."""

import pytest
from sqlalchemy.engine import make_url

from heliogeo.core.config import Settings

_DB_ENV = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)


@pytest.fixture(autouse=True)
def clean_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные окружения разработчика не должны влиять на тест."""
    for name in _DB_ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_compose_defaults() -> None:
    """Без настроек DSN указывает на локальную БД из docker compose (порт 5433)."""
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://heliogeo:heliogeo@localhost:5433/heliogeo"


def test_dsn_is_built_from_parts() -> None:
    """DSN собирается из POSTGRES_*."""
    settings = Settings(
        _env_file=None,
        POSTGRES_USER="app",
        POSTGRES_PASSWORD="secret",
        POSTGRES_HOST="db.internal",
        POSTGRES_PORT=6432,
        POSTGRES_DB="space",
    )
    assert settings.database_url == "postgresql+psycopg://app:secret@db.internal:6432/space"


def test_special_characters_in_password_are_escaped() -> None:
    """Спецсимволы в пароле экранируются и после разбора восстанавливаются."""
    settings = Settings(_env_file=None, POSTGRES_PASSWORD="p@ss/w:rd")
    assert "p@ss/w:rd" not in settings.database_url
    assert make_url(settings.database_url).password == "p@ss/w:rd"


def test_explicit_database_url_wins() -> None:
    """Полный DATABASE_URL приоритетнее POSTGRES_*."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://u:p@cloud:6432/prod?sslmode=verify-full",
        POSTGRES_USER="ignored",
    )
    assert settings.database_url == "postgresql://u:p@cloud:6432/prod?sslmode=verify-full"


def test_parts_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSTGRES_* читаются из переменных окружения."""
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    settings = Settings(_env_file=None)
    assert settings.postgres_port == 6543
    assert settings.database_url.endswith("@localhost:6543/heliogeo")
