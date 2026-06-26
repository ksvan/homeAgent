set dotenv-load := false

default:
    just --list

check-ci:
    uv run ruff check app/
    uv run ruff format --check app/
    uv run pytest tests/unit/ -v --tb=short

check-fast:
    uv run ruff check app tests
    uv run ruff format --check app tests
    uv run pytest tests/unit/ -v --tb=short

check-types:
    uv run mypy app/

test-unit:
    uv run pytest tests/unit/ -v --tb=short

test-all:
    uv run pytest tests/ -v --tb=short

lint:
    uv run ruff check app tests

format-check:
    uv run ruff format --check app tests

format:
    uv run ruff check app tests --fix
    uv run ruff format app tests

migrate:
    uv run alembic upgrade head

run-dev:
    APP_ENV=development uv run python -m app
