.PHONY: help install setup-hooks format lint type-check security codeql codeql-setup codeql-clean openapi openapi-check pre-commit test test-cov \
	deps dev up down logs run migrate-create migrate-up \
	e2e-dev e2e-prod e2e-auth e2e-both ci-check clean

help:
	@echo "Available commands:"
	@echo "  make install         - Install all dependencies (uv sync)"
	@echo "  make setup-hooks     - Install pre-commit git hooks"
	@echo "  make format          - Format code with ruff"
	@echo "  make lint            - Lint code with ruff"
	@echo "  make type-check      - Run ty type checking"
	@echo "  make security        - Run security checks with bandit"
	@echo "  make codeql          - Run CodeQL locally with the CI query set (grouped report)"
	@echo "  make codeql-setup    - Install the CodeQL CLI bundle (same release as GitHub Actions)"
	@echo "  make openapi         - Regenerate docs/openapi.json from the app (same export as CI)"
	@echo "  make pre-commit      - One command before committing: fix format + all fast gates"
	@echo "  make ci-check        - Pre-commit gates + CodeQL (slow) — run before pushing"
	@echo "  make test            - Run unit + integration tests"
	@echo "  make test-cov        - Run tests with coverage"
	@echo "  make deps            - Start PostgreSQL + Redis only (for local runs)"
	@echo "  make dev             - Start dev stack in Docker (no Redis, hot reload)"
	@echo "  make up              - Start full stack in Docker (Redis workers)"
	@echo "  make down            - Tear down the Docker stack"
	@echo "  make logs            - Tail app container logs"
	@echo "  make run             - Run the server locally with hot reload (needs make deps)"
	@echo "  make migrate-create MSG=\"...\" - Create a new alembic migration"
	@echo "  make migrate-up      - Apply migrations (alembic upgrade head)"
	@echo "  make e2e-dev         - Run E2E tests in dev mode (no Redis)"
	@echo "  make e2e-prod        - Run E2E tests in prod mode (Redis workers)"
	@echo "  make e2e-auth        - Run auth E2E tests (JWT mock auth enabled)"
	@echo "  make e2e-both        - Run E2E tests in both modes"
	@echo "  make ci-check        - Run all CI checks locally"
	@echo "  make clean           - Clean cache files"

install:
	uv sync

setup-hooks:
	uv run pre-commit install
	uv run pre-commit install --hook-type commit-msg

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

type-check:
	uv run ty check src/agent_server/ src/graphs/ src/hub/ --exit-zero-on-warning

arch-check:
	uv run python scripts/check_architecture.py

security:
	uv run bandit -c pyproject.toml -r src/agent_server/

# Same engine + same query suites as .github/workflows/codeql.yml (config lives
# in .github/codeql/codeql-config.yml, shared by CI and this target).
# Outputs: .codeql/results.sarif (raw SARIF) + .codeql/codeql-report.md (grouped).
codeql:
	uv run python scripts/codeql_local.py analyze

codeql-setup:
	uv run python scripts/codeql_local.py ensure-cli

codeql-clean:
	rm -rf .codeql

openapi:
	uv run python scripts/export_openapi.py

# Regenerate the spec in place; fails if it changed, so drift never reaches CI.
# On failure the fix is already in the working tree — just commit it.
openapi-check:
	uv run python scripts/export_openapi.py
	@git diff --exit-code -- docs/openapi.json || \
		{ echo "ERROR: docs/openapi.json drifted — regenerated in the working tree, commit it."; exit 1; }

# Everything that should pass before you commit. Auto-fixes formatting first,
# then every fast gate; bandit stays non-blocking (repo convention).
# CodeQL is deliberately excluded — minutes, not seconds (`make codeql`).
pre-commit: format
	$(MAKE) lint type-check arch-check
	-$(MAKE) security
	$(MAKE) openapi-check test
	@echo ""
	@echo "Pre-commit gates passed — ready to commit."

test:
	uv run pytest tests/unit tests/integration tests/graphs tests/shop tests/ml tests/hub

test-cov:
	uv run pytest tests/unit tests/integration tests/graphs tests/shop tests/ml tests/hub --cov=src/agent_server --cov=src/graphs --cov-report=html --cov-report=term

deps:
	docker compose up -d postgres redis

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

up:
	docker compose up -d

down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down

logs:
	docker compose logs -f app

run:
	uv run uvicorn --env-file .env agent_server.app.main:app --reload --host 0.0.0.0 --port 2026

# CHAIN=hub targets the business chain in src/hub (its own version table);
# default is the framework chain. Never point one chain's env at the other.
migrate-create:
ifeq ($(CHAIN),hub)
	cd src/hub && uv run alembic -c alembic.ini revision --autogenerate -m "$(MSG)"
else
	cd src/agent_server && uv run alembic -c alembic.ini revision --autogenerate -m "$(MSG)"
endif

migrate-up:
	cd src/agent_server && uv run alembic -c alembic.ini upgrade head
	cd src/hub && uv run alembic -c alembic.ini upgrade head

ci-check: pre-commit
	$(MAKE) codeql
	@echo ""
	@echo "All CI checks completed!"

E2E_IGNORE := --ignore=tests/e2e/manual_auth_tests --ignore=tests/e2e/multi_instance

e2e-dev:
	@echo "Starting dev mode (LocalExecutor, no Redis)..."
	@docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml up -d
	@echo "Waiting for server..."; \
	ready=0; \
	for i in $$(seq 1 30); do \
		if curl -s http://localhost:2026/health > /dev/null 2>&1; then ready=1; break; fi; \
		sleep 2; \
	done; \
	if [ "$$ready" = "0" ]; then \
		echo "Server failed to start within 60s"; \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml logs --tail=80; \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml down; \
		exit 1; \
	fi
	@rc=0; \
	uv run pytest tests/e2e/ -m "not prod_only" $(E2E_IGNORE) -v --tb=short || rc=$$?; \
	if [ "$$rc" != "0" ]; then docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml logs --tail=200; fi; \
	docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml down; \
	exit $$rc

e2e-prod:
	@echo "Starting prod mode (WorkerExecutor + Redis)..."
	@docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d
	@echo "Waiting for server..."; \
	ready=0; \
	for i in $$(seq 1 30); do \
		if curl -s http://localhost:2026/health > /dev/null 2>&1; then ready=1; break; fi; \
		sleep 2; \
	done; \
	if [ "$$ready" = "0" ]; then \
		echo "Server failed to start within 60s"; \
		docker compose -f docker-compose.yml -f docker-compose.e2e.yml logs --tail=80; \
		docker compose -f docker-compose.yml -f docker-compose.e2e.yml down; \
		exit 1; \
	fi
	@rc=0; \
	uv run pytest tests/e2e/ $(E2E_IGNORE) -v --tb=short || rc=$$?; \
	if [ "$$rc" != "0" ]; then docker compose -f docker-compose.yml -f docker-compose.e2e.yml logs --tail=200; fi; \
	docker compose -f docker-compose.yml -f docker-compose.e2e.yml down; \
	exit $$rc

e2e-auth:
	@echo "Starting auth mode (JWT mock auth, LocalExecutor)..."
	@docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml -f docker-compose.auth.yml up -d postgres app
	@echo "Waiting for server..."; \
	ready=0; \
	for i in $$(seq 1 45); do \
		if curl -s http://localhost:2026/health > /dev/null 2>&1; then ready=1; break; fi; \
		sleep 2; \
	done; \
	if [ "$$ready" = "0" ]; then \
		echo "Server failed to start within 90s"; \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml -f docker-compose.auth.yml logs --tail=80; \
		docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml -f docker-compose.auth.yml down; \
		exit 1; \
	fi
	@rc=0; \
	uv run pytest tests/e2e/manual_auth_tests/ -v --tb=short -m auth_only -o addopts="--strict-markers -ra --color=yes" || rc=$$?; \
	if [ "$$rc" != "0" ]; then docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml -f docker-compose.auth.yml logs --tail=200; fi; \
	docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.e2e.yml -f docker-compose.auth.yml down; \
	exit $$rc

e2e-both: e2e-dev e2e-prod

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ty_cache .ruff_cache htmlcov 2>/dev/null || true
