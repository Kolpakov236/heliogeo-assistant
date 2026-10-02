"""Интеграционные тесты миграций Alembic: накат, откат, согласованность с моделями."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from heliogeo.storage.models import Base
from tests.integration.helpers import current_version, make_alembic_config, tables

pytestmark = pytest.mark.db


def test_upgrade_head_then_downgrade_base(empty_db_url: str) -> None:
    """upgrade head доводит БД до последней ревизии, downgrade base убирает все таблицы."""
    cfg = make_alembic_config(empty_db_url)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    command.upgrade(cfg, "head")
    assert current_version(empty_db_url) == head

    command.downgrade(cfg, "base")
    assert tables(empty_db_url) == {"alembic_version"}


def test_every_revision_upgrades_and_downgrades(empty_db_url: str) -> None:
    """Каждая ревизия: накат → откат → накат (ловит необратимые и неполные downgrade)."""
    cfg = make_alembic_config(empty_db_url)
    script = ScriptDirectory.from_config(cfg)
    for rev in reversed(list(script.walk_revisions())):  # от старых к новым
        command.upgrade(cfg, rev.revision)
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, rev.revision)
    assert current_version(empty_db_url) == script.get_current_head()


def test_models_match_migrated_schema(migrated_engine: Engine) -> None:
    """Модели и миграции не разошлись: autogenerate не находит отличий."""
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
