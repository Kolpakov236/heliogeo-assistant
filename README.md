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

## Контракты

Доменные данные, которые подписывает эксперт (реестр фактов, словарь терминов, пороги,
спецификации признаков), лежат в `contracts/` и версионируются:

    make contracts        # проверить контракты (входит и в make lint)
    make contracts-lock   # обновить versions.lock.json после повышения version

Подробнее: docs/contracts.md.

## Облачный контур

Целевой запуск — Yandex Cloud без постоянно работающих серверов: Workflows оркестрирует Cloud
Functions, объёмные данные лежат в Object Storage, оперативное состояние — в YDB. Схема, общие
правила и лимиты — docs/architecture.md; спецификация суточного воркфлоу —
deploy/workflows/daily.yaml; таблицы YDB — docs/database.md.

## Структура

См. docs/architecture.md.
