# Local development. Postgres runs in Docker on port 5433 (alongside ClinicVoice on 5432).
.PHONY: setup db-up db-down db-reset migrate seed seed-demo synth api worker web test e2e eval lint typecheck check

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

seed:             ## Protocols (+ embeddings), demo users, gold set
	cd backend && uv run python -m intake.seed

seed-demo:        ## seed, then reset live cases and load the demo queue
	cd backend && SEED_ALLOW_RESET=1 uv run python -m intake.seed --demo

synth:            ## Regenerate the gold set PDFs and labels from the scenarios
	cd backend && uv run python -m intake.synth

eval:             ## Full evaluation over gold.v1 (set PROMPTS="triage=v2" to compare)
	cd backend && uv run python -m intake.eval --gold v1 $(if $(PROMPTS),--prompts $(PROMPTS),)

worker:           ## Run the pipeline worker on its own (when the API has RUN_WORKER=false)
	cd backend && uv run python -m intake.pipeline.worker

api:              ## Run the API (and worker thread) with reload on :8000
	cd backend && uv run uvicorn intake.api.app:app --reload --port 8000

web:              ## Run the web app on :5174 (proxies /api to :8000)
	cd web && pnpm dev

test:
	cd backend && uv run pytest
	cd web && pnpm test

e2e:              ## Playwright end-to-end tests (own database and servers, dev-oracle)
	cd web && pnpm e2e

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd backend && uv run mypy intake tests
	cd web && pnpm typecheck

check: lint typecheck test   ## Everything CI runs, locally
