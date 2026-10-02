"""Подключение к PostgreSQL: нормализация URL, engine, проверка соединения."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from heliogeo.core.config import get_settings


def normalize_url(url: str) -> str:
    """Привести URL к драйверу psycopg 3.

    ``postgresql://`` без драйвера SQLAlchemy трактует как psycopg2, которого в проекте нет,
    поэтому подставляем ``postgresql+psycopg://``. Остальные части URL не меняются.
    """
    if not url:
        raise ValueError("URL базы данных не задан (DATABASE_URL или POSTGRES_*)")
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine приложения (кэшируется); URL берётся из настроек."""
    return create_engine(normalize_url(get_settings().database_url), pool_pre_ping=True)


def ping(engine: Engine | None = None) -> str:
    """Проверить соединение с БД и вернуть версию сервера (SHOW server_version)."""
    with (engine or get_engine()).connect() as connection:
        return str(connection.execute(text("SHOW server_version")).scalar_one())
