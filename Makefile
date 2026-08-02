.PHONY: bootstrap check format lint schemas type test test-lean test-postgres migrate

bootstrap:
	python3.12 -m venv .venv
	.venv/bin/python -m pip install -U pip
	.venv/bin/python -m pip install -e ".[dev,gcs]"

format:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .

lint:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .

type:
	.venv/bin/mypy padawan

schemas:
	PYTHONPATH=. .venv/bin/python scripts/generate_schemas.py --check

test:
	PYTHONPATH=. .venv/bin/pytest -m "not postgres and not live and not lean"

test-lean:
	PYTHONPATH=. .venv/bin/pytest -m lean

test-postgres:
	PYTHONPATH=. .venv/bin/pytest -m postgres

check: lint schemas type test

migrate:
	.venv/bin/alembic upgrade head
