"""Интеграционные тесты подключения к PostgreSQL (нужен `make up`)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import Engine

from heliogeo.storage import db

pytestmark = pytest.mark.db


def test_ping_returns_server_version(admin_engine: Engine) -> None:
    """ping() возвращает версию сервера."""
    version = db.ping(admin_engine)
    assert version[0].isdigit()


def test_get_engine_uses_database_url_from_settings(
    migrated_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine приложения строится по database_url из настроек."""
    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(database_url=migrated_db_url))
    db.get_engine.cache_clear()
    try:
        assert db.ping()
    finally:
        db.get_engine().dispose()
        db.get_engine.cache_clear()
