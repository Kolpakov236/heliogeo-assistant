# Heliogeo Assistant

Автоматизированная система помощи гелиогеофизику.

## Быстрый старт

    poetry install
    cp .env.example .env
    # заполнить .env
    poetry run heliogeo --help

## База данных (PostgreSQL)

    make up        # PostgreSQL 17 в docker compose (127.0.0.1:5433)
    make migrate   # применить миграции Alembic
    make test-db   # тесты, которым нужна БД

Значения по умолчанию из `.env.example` подходят для локальной БД. Подробнее: docs/database.md.

## Структура

См. docs/architecture.md.
