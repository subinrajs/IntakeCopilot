# Local development. Postgres runs in Docker on port 5433 (alongside ClinicVoice on 5432).
.PHONY: setup db-up db-down db-reset migrate api web test lint typecheck check

setup:            ## Install backend and web dependencies
	cd backend && uv sync
	cd web && pnpm install

db-up:            ## Start Postgres (pgvector) and wait until it is healthy
	docker compose up -d --wait postgres

db-down:
	docker compose down

db-reset:         ## Drop the local database volume and start fresh
	docker compose down -v
	$(MAKE) db-up migrate

migrate:          ## Apply SQL migrations as the schema owner
	cd backend && uv run python -m intake.migrate

api:              ## Run the API with reload on :8000
	cd backend && uv run uvicorn intake.api.app:app --reload --port 8000

web:              ## Run the web app on :5174 (proxies /api to :8000)
	cd web && pnpm dev

test:
	cd backend && uv run pytest
	cd web && pnpm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd backend && uv run mypy intake tests
	cd web && pnpm typecheck

check: lint typecheck test   ## Everything CI runs, locally
