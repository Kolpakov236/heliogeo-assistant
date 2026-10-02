"""Тесты нормализации URL базы данных (heliogeo.storage.db.normalize_url)."""

import pytest

from heliogeo.storage.db import normalize_url


def test_plain_postgresql_gets_psycopg_driver() -> None:
    """postgresql:// без драйвера превращается в postgresql+psycopg://."""
    assert (
        normalize_url("postgresql://u:p@localhost:5433/db")
        == "postgresql+psycopg://u:p@localhost:5433/db"
    )


def test_explicit_driver_is_kept() -> None:
    """Явно указанный драйвер не меняется."""
    url = "postgresql+psycopg://u:p@localhost:5433/db"
    assert normalize_url(url) == url


def test_query_parameters_are_kept() -> None:
    """Параметры запроса (sslmode и т.п.) сохраняются."""
    assert (
        normalize_url("postgresql://u:p@host:6432/db?sslmode=verify-full")
        == "postgresql+psycopg://u:p@host:6432/db?sslmode=verify-full"
    )


def test_percent_encoded_password_survives() -> None:
    """Экранированные спецсимволы в пароле не портятся при нормализации."""
    url = "postgresql+psycopg://u:p%40ss%2Fw%3Ard@host/db"
    assert normalize_url(url) == url


def test_empty_url_is_rejected() -> None:
    """Пустой URL — понятная ошибка, а не молчаливое подключение в никуда."""
    with pytest.raises(ValueError, match="не задан"):
        normalize_url("")
