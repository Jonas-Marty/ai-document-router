# AI Document Router — task runner
# Install just:  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to ~/.local/bin

set dotenv-load := true
set shell := ["bash", "-uc"]
# WSL2's $XDG_RUNTIME_DIR (just's default for shebang scratch files) is sometimes mounted
# noexec, which breaks every shebang recipe below with "Permission denied (os error 13)".
set tempdir := "/tmp"

default:
    @just --list

# --- setup -------------------------------------------------------------------

# One-time setup after cloning
setup: setup-api setup-web
    @echo "Setup complete. Copy .env.example to .env and fill it in, then run: just upgrade"

setup-api:
    cd backend && uv sync

setup-web:
    cd frontend && pnpm install

# --- development -------------------------------------------------------------

# Run backend and frontend together with hot reload
dev:
    #!/usr/bin/env bash
    set -uo pipefail
    trap 'kill 0' EXIT INT TERM
    just dev-api &
    just dev-web &
    wait

dev-api:
    cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
    cd frontend && pnpm dev --host

# --- database ----------------------------------------------------------------

# Generate a migration:  just migrate "add skip_count to document"
migrate message:
    cd backend && uv run alembic revision --autogenerate -m "{{message}}"

upgrade:
    cd backend && uv run alembic upgrade head

downgrade:
    cd backend && uv run alembic downgrade -1

# Delete the local database and rebuild it from migrations
reset-db:
    rm -f backend/data/app.db backend/data/app.db-wal backend/data/app.db-shm
    just upgrade

# --- quality -----------------------------------------------------------------

# Run before declaring any task done
check: lint typecheck test

lint:
    cd backend && uv run ruff check --fix . && uv run ruff format .
    cd frontend && pnpm biome check --write .

typecheck:
    cd backend && uv run mypy app
    cd frontend && pnpm exec tsc -b

test: test-api test-web

test-api:
    cd backend && uv run pytest -q

test-web:
    cd frontend && pnpm vitest run

test-watch:
    cd frontend && pnpm vitest

# --- deployment --------------------------------------------------------------

build:
    docker compose -f deploy/docker-compose.yml build

up:
    docker compose -f deploy/docker-compose.yml up -d

down:
    docker compose -f deploy/docker-compose.yml down

logs:
    docker compose -f deploy/docker-compose.yml logs -f
