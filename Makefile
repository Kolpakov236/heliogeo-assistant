.PHONY: install lint format test run clean up down migrate migrate-down revision test-db contracts contracts-lock

install:
	poetry install

lint:
	poetry run ruff check src tests
	poetry run mypy src
	poetry run python -m heliogeo.contracts.validate
	poetry run lint-imports --no-logo

format:
	poetry run ruff format src tests

test:
	poetry run pytest

run:
	poetry run heliogeo run --product all

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .import_linter_cache .coverage htmlcov

# --- PostgreSQL (docker compose) и миграции Alembic ---
# --project-directory . — чтобы compose читал корневой .env
COMPOSE = docker compose --project-directory . -f deploy/compose/docker-compose.yml

up:
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

migrate:
	poetry run alembic upgrade head

migrate-down:
	poetry run alembic downgrade -1

# make revision m="описание" [id=0003]
revision:
ifndef m
	$(error Укажите описание: make revision m="что делает миграция" [id=0003])
endif
	poetry run alembic revision -m "$(m)" $(if $(id),--rev-id $(id))

# Тесты, которым нужна БД (нужен make up); обычный make test их пропускает
test-db:
	poetry run pytest -m db

# --- Контракты (contracts/): проверка и обновление versions.lock.json ---
contracts:
	poetry run python -m heliogeo.contracts.validate

# Запускать после повышения version у изменённого контракта
contracts-lock:
	poetry run python -m heliogeo.contracts.lock
