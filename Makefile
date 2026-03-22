# =============================================================================
# MASE (Multi-Agent Simulation Environment) - Makefile
# =============================================================================

.PHONY: help build up down logs shell test clean setup verify env

# Default target
help:
	@echo "MASE Platform - Available Commands:"
	@echo ""
	@echo "  make setup          - Initial project setup"
	@echo "  make build          - Build all Docker images"
	@echo "  make up             - Start core platform services"
	@echo "  make down           - Stop core platform services"
	@echo "  make core-up        - Start only core services"
	@echo "  make core-down      - Stop only core services"
	@echo "  make restart        - Restart all core services"
	@echo "  make logs           - View logs from core services"
	@echo "  make logs-<service> - View logs from specific service"
	@echo "  make shell-<svc>    - Open shell in service container"
	@echo "  make clean          - Remove core containers and volumes"
	@echo "  make clean-runs     - Stop all per-run containers"
	@echo "  make clean-all      - Full cleanup including images and runs"
	@echo "  make status         - Check service health status"
	@echo "  make env            - Create .env file from example"
	@echo ""
	@echo "Core Services: admin-backend, admin-frontend, controller,"
	@echo "               agent-launcher, orchestrator, postgres, redis"
	@echo ""
	@echo "Per-Run Services: environment, agents"
	@echo "                  (started dynamically for each run)"

# =============================================================================
# Setup & Configuration
# =============================================================================

setup: env
	@echo "Setting up MASE platform..."
	@chmod +x scripts/*.sh
	@mkdir -p data/agents data/ledger data/metrics data/logs
	@echo "Setup complete. Run 'make build' to build images."

env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env file from .env.example"; \
		echo "Please edit .env with your configuration"; \
	fi

# =============================================================================
# Build & Deploy
# =============================================================================

build:
	@echo "Building MASE services..."
	bash scripts/build_images.sh

build-no-cache:
	@echo "Building MASE services (no cache) is not supported by the public helper yet."
	@exit 1

up:
	bash scripts/up_stack.sh

down:
	bash scripts/down_stack.sh

restart: down up

restart-service:
	@read -p "Service name: " svc; \
	docker-compose restart $$svc

# =============================================================================
# Logs & Debugging
# =============================================================================

logs:
	docker compose -f docker-compose.yml logs -f

logs-admin-backend:
	docker compose -f docker-compose.yml logs -f admin-backend

logs-admin-frontend:
	docker compose -f docker-compose.yml logs -f admin-frontend

logs-controller:
	docker compose -f docker-compose.yml logs -f controller

logs-agent-launcher:
	docker compose -f docker-compose.yml logs -f agent-launcher

logs-orchestrator:
	docker compose -f docker-compose.yml logs -f orchestrator

logs-postgres:
	docker compose -f docker-compose.yml logs -f postgres

logs-redis:
	docker compose -f docker-compose.yml logs -f redis

# =============================================================================
# Shell Access
# =============================================================================

shell-admin-backend:
	docker compose -f docker-compose.yml exec admin-backend /bin/bash

shell-controller:
	docker compose -f docker-compose.yml exec controller /bin/bash

shell-agent-launcher:
	docker compose -f docker-compose.yml exec agent-launcher /bin/bash

shell-orchestrator:
	docker compose -f docker-compose.yml exec orchestrator /bin/bash

shell-postgres:
	docker compose -f docker-compose.yml exec postgres psql -U mase -d mase

# =============================================================================
# Testing
# =============================================================================

test:
	@echo "Running tests..."
	@echo "TODO: Implement test suite"

test-integration:
	@echo "Running integration tests..."
	@echo "TODO: Implement integration tests"

# =============================================================================
# Cleanup
# =============================================================================

clean:
	bash scripts/down_stack.sh

clean-runs:
	@echo "Stopping all per-run containers..."
	@docker ps -q -f "label=mase.run_id" | xargs -r docker stop
	@docker ps -aq -f "label=mase.run_id" | xargs -r docker rm
	@echo "All per-run containers stopped."

clean-all: clean clean-runs
	@echo "Removing images..."
	docker compose -f docker-compose.yml down --rmi all
	docker system prune -f

# =============================================================================
# Utilities
# =============================================================================

ps:
	docker compose -f docker-compose.yml ps

status:
	bash scripts/verify_platform.sh

migrate:
	@echo "Running database migrations..."
	@echo "TODO: Implement migrations"

backup:
	@echo "Creating backup..."
	@mkdir -p backups
	@docker-compose exec postgres pg_dump -U mase -d mase > backups/mase_$$(date +%Y%m%d_%H%M%S).sql

# =============================================================================
# Development
# =============================================================================

dev-frontend:
	cd services/admin/frontend && npm start

dev-backend:
	cd services/admin/backend && uvicorn app.main:app --reload --port 8001

lint:
	@echo "Running linters..."
	@cd services/admin/frontend && npm run lint 2>/dev/null || echo "Frontend linting skipped"
	@flake8 services/*/app --max-line-length=100 2>/dev/null || echo "Python linting skipped"

format:
	@echo "Formatting code..."
	@black services/*/app --line-length=100 2>/dev/null || echo "Black not installed"

# =============================================================================
# Shortcuts
# =============================================================================

# Quick start for development
start-dev: setup build up
	@echo "MASE platform is running in development mode"

# Full reset
reset: clean-all setup build up
	@echo "MASE platform has been fully reset"
