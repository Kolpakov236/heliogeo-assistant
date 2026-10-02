"""Общие помощники интеграционных тестов с PostgreSQL."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]


def tables(url: str) -> set[str]:
    """Имена таблиц схемы public в БД по URL."""
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def current_version(url: str) -> str | None:
    """Текущая ревизия Alembic в БД (None, если таблица версий пуста)."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def make_alembic_config(url: str) -> Config:
    """Конфигурация Alembic, направленная на заданную БД.

    Логи Alembic в тестах не настраиваем (configure_logger=False): иначе fileConfig
    сбрасывает обработчики корневого логгера и ломает перехват логов в pytest.
    """
    cfg = Config(str(ROOT / "alembic.ini"))
    # % в URL (экранированный пароль) нужно удвоить: ConfigParser делает интерполяцию
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg
