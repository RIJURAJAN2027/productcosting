# Common tasks. Backend commands use ./backend/.venv (make venv creates it).
PY ?= backend/.venv/bin/python
.PHONY: help venv dev-api dev-ui test test-pg typecheck build up down logs backup

help:
	@echo "make venv       create backend/.venv and install the backend and frontend dependencies"
	@echo "make dev-api    run the API on :8000 with a local SQLite database and the demo company"
	@echo "make dev-ui     run the UI on :5173 (proxies /api to :8000)"
	@echo "make test       backend tests (set PC_LEGACY_WORKBOOK=/path/workbook.xlsx for the regression test)"
	@echo "make typecheck  TypeScript check of the UI"
	@echo "make build      build the Docker image"
	@echo "make up / down  start / stop the application and database with docker compose"
	@echo "make backup     pg_dump the database into ./backups"

venv:
	python3 -m venv backend/.venv
	$(PY) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

dev-api:
	cd backend && PC_ENV=development PC_DATABASE_URL=sqlite:///./dev.db .venv/bin/python -m uvicorn app.main:app --reload --port 8000

dev-ui:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest

test-pg:
	@echo "Runs the API tests on PostgreSQL: set PC_TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db (it is wiped)"
	cd backend && .venv/bin/python -m pytest tests/test_api.py

typecheck:
	cd frontend && npx tsc --noEmit

build:
	docker build -t productcosting:latest .

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

backup:
	scripts/backup-db.sh
