.PHONY: install lint format test run clean

install:
	poetry install

lint:
	poetry run ruff check src tests
	poetry run mypy src

format:
	poetry run ruff format src tests

test:
	poetry run pytest

run:
	poetry run heliogeo run --product all

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
