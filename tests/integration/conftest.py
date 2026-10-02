"""Фикстуры интеграционных тестов с PostgreSQL (маркер db).

Тесты не трогают рабочую БД: на сервере из TEST_DATABASE_URL (или DATABASE_URL / POSTGRES_*
из .env) создаётся одноразовая база heliogeo_test_<hex>, в ней гоняются миграции, а после
тестов она удаляется. Сервер поднимает ``make up``, тесты запускает ``make test-db``.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from heliogeo.core.config import get_settings
from heliogeo.storage.db import normalize_url
from tests.integration.helpers import make_alembic_config


@contextmanager
def _temp_database(admin_engine: Engine, server_url: URL) -> Iterator[URL]:
    """Создать одноразовую БД на сервере и удалить её по выходу."""
    name = f"heliogeo_test_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server_url.set(database=name)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def server_url() -> URL:
    """URL сервера PostgreSQL для тестов (нужно право создавать базы)."""
    raw = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url
    return make_url(normalize_url(raw))


@pytest.fixture(scope="session")
def admin_engine(server_url: URL) -> Iterator[Engine]:
    """Engine к служебной БД postgres (AUTOCOMMIT: CREATE/DROP DATABASE вне транзакции)."""
    engine = create_engine(server_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect():
            pass
    except OperationalError as exc:
        engine.dispose()
        pytest.fail(
            f"PostgreSQL недоступен ({server_url.render_as_string()}): "
            f"запустите `make up` или задайте TEST_DATABASE_URL.\n{exc}"
        )
    yield engine
    engine.dispose()


@pytest.fixture
def empty_db_url(admin_engine: Engine, server_url: URL) -> Iterator[str]:
    """Пустая одноразовая БД без миграций (для тестов самих миграций)."""
    with _temp_database(admin_engine, server_url) as url:
        yield url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated_db_url(admin_engine: Engine, server_url: URL) -> Iterator[str]:
    """Одноразовая БД, накатанная до head (общая для тестов схемы и данных)."""
    with _temp_database(admin_engine, server_url) as url:
        raw = url.render_as_string(hide_password=False)
        command.upgrade(make_alembic_config(raw), "head")
        yield raw


@pytest.fixture(scope="session")
def migrated_engine(migrated_db_url: str) -> Iterator[Engine]:
    """Engine к БД, накатанной до head."""
    engine = create_engine(migrated_db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(migrated_engine: Engine) -> Iterator[Session]:
    """Сессия в транзакции, откатываемой после теста: данные между тестами не копятся."""
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db_session:
            yield db_session
        transaction.rollback()
