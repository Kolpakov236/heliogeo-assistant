"""Тесты heliogeo.storage.db без БД: engine и ping на подменённых объектах."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from heliogeo.storage.db import get_engine, ping


def test_ping_returns_server_version() -> None:
    """ping() выполняет SHOW server_version и возвращает строку версии."""
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = "17.11"

    assert ping(engine) == "17.11"
    engine.connect.assert_called_once_with()


def test_ping_propagates_connection_errors() -> None:
    """Недоступная БД — исключение, а не молчаливый False."""
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("connection refused"))

    with pytest.raises(OperationalError):
        ping(engine)


def test_get_engine_is_built_from_settings_and_cached() -> None:
    """Engine строится по database_url из настроек (с драйвером psycopg) и кэшируется."""
    get_engine.cache_clear()
    try:
        with (
            patch("heliogeo.storage.db.get_settings") as get_settings,
            patch("heliogeo.storage.db.create_engine") as create_engine,
        ):
            get_settings.return_value.database_url = "postgresql://u:p@h:5433/d"
            first = get_engine()
            second = get_engine()
    finally:
        get_engine.cache_clear()

    create_engine.assert_called_once_with("postgresql+psycopg://u:p@h:5433/d", pool_pre_ping=True)
    assert first is second
