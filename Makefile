# artha-engine developer entrypoints. Usage: make <target>

.PHONY: help up down venv migrate demo backend frontend test lint check clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up:  ## Start PostgreSQL (Docker)
	docker compose up -d postgres

down:  ## Stop all containers
	docker compose down

venv:  ## Create backend/.venv and install dependencies
	cp -n .env.example backend/.env 2>/dev/null || true
	cd backend && python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt

migrate:  ## Run database migrations
	cd backend && .venv/bin/alembic upgrade head

demo: migrate  ## Load the fictional demo household (login: demo@example.com / demo-password)
	cd backend && .venv/bin/python scripts/seed_demo.py

backend:  ## Serve the API on :8000
	cd backend && .venv/bin/uvicorn main:app --reload --port 8000

frontend:  ## Install deps and serve the web app on :5173
	cd frontend && npm install && npm run dev

test:  ## Run backend tests (SQLite in memory, no network, no API key)
	cd backend && .venv/bin/pytest -q

lint:  ## Lint backend with ruff
	cd backend && .venv/bin/ruff check .

check: lint test  ## Lint, tests, and the frontend palette guard
	cd frontend && node scripts/check-palette.mjs

clean:  ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.ruff_cache backend/.pytest_cache frontend/dist
