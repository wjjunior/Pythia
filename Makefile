# Pythia — local run / DX control.
# Quickstart: `make up`, then `make api` in one terminal (and optionally `make worker` in another).
# Everything runs zero-secret in paper mode — the stub LLM and recorded fixtures need no keys.

COMPOSE   := docker compose
DASHBOARD := npm --prefix dashboard

# Config is read from .env by the app and Alembic themselves (pydantic-settings, env_file=".env").
# The Makefile deliberately does NOT export .env into recipes, so `make test` runs with a clean
# environment (the suite asserts a zero-secret boot). Copy .env.example → .env before `make up`.

.DEFAULT_GOAL := help
.PHONY: help install up down reset-db migrate config api worker \
        lint format typecheck test test-integration verify \
        dashboard dashboard-build hooks clean

help: ## Show available commands
	@echo "Pythia — local run / DX control"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Core path needs no secrets. 'make up' = ready DB; 'make api' serves REST + SSE on :8000."

install: ## Install all dependencies into the uv venv (incl. dev)
	uv sync --dev

up: ## Start Postgres + pgvector (--wait) and migrate → ready database
	$(COMPOSE) up -d --wait db
	uv run alembic upgrade head
	@echo "Postgres up + migrated on :5434 (DATABASE_URL is read from .env)."

down: ## Stop Postgres, keeping the data volume
	$(COMPOSE) down

reset-db: ## Drop the Postgres volume, recreate, and migrate (fresh database)
	$(COMPOSE) down -v
	$(COMPOSE) up -d --wait db
	uv run alembic upgrade head

migrate: ## Apply Alembic migrations to head (Postgres must be up)
	uv run alembic upgrade head

config: ## Print the boot/config check (mode, live venues, llm, db)
	uv run python -m pythia.config

api: ## Run the FastAPI REST + SSE app on :8000 (OpenAPI at /docs)
	uv run python -m pythia.api

worker: ## Run the scheduler/worker (in-process APScheduler crawl + pipeline)
	uv run python -m pythia.worker

lint: ## Lint with ruff
	uv run ruff check .

format: ## Format sources with ruff
	uv run ruff format .

typecheck: ## Type-check with mypy (strict)
	uv run mypy

test: ## Run the pure unit/property suite (no Docker, no secrets)
	uv run pytest

test-integration: ## Run the integration suite (testcontainers; needs Docker)
	uv run pytest -m integration

verify: ## Lint + format-check + typecheck + unit test (the CI gate)
	uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest

# Install the dashboard deps only when the manifests change so re-runs stay fast.
dashboard/node_modules: dashboard/package.json dashboard/package-lock.json
	$(DASHBOARD) ci
	@touch dashboard/node_modules

dashboard: dashboard/node_modules ## Run the dashboard dev server (Vite; proxies the API on :8000)
	$(DASHBOARD) run dev

dashboard-build: dashboard/node_modules ## Generate TS types from OpenAPI, typecheck and bundle the dashboard
	$(DASHBOARD) run build

hooks: ## Install the pre-commit hooks (ruff + mypy on commit)
	uv run pre-commit install

clean: ## Remove caches, the venv and dashboard build output
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dashboard/node_modules dashboard/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
