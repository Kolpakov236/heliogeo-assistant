"""Базовый класс ORM-моделей и соглашение об именах ограничений."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Соглашение об именах: стабильные имена PK/UNIQUE/CHECK/FK/индексов. Миграции задают
# те же имена явно (через op.f), поэтому схема из миграций и из моделей совпадает.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей heliogeo."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
