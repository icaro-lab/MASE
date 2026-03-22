# =============================================================================
# MASE (Multi-Agent Simulation Environment) - Makefile
# =============================================================================

.PHONY: help build up down logs shell test clean setup

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
	docker-compose build

build-no-cache:
	@echo "Building MASE services (no cache)..."
	docker-compose build --no-cache

up:
	@echo "Starting MASE platform (core services)..."
	docker-compose up -d
	@echo "Core services starting..."
	@echo "Admin Dashboard: http://localhost:3016"
	@echo "Admin API:       http://localhost:8001"
	@echo "Controller:      http://localhost:8002"
	@echo "Agent Launcher:  http://localhost:8004"
	@echo "Orchestrator:    http://localhost:8006"
	@echo ""
	@echo "Note: Per-run services (environments, agents) are started dynamically"
	@echo "      when runs are created."

down:
	@echo "Stopping MASE platform..."
	docker-compose down
	@echo "Note: Per-run containers may still be running."
	@echo "      Use 'make clean-runs' to stop all runs."

core-up:
	@echo "Starting core platform services only..."
	docker-compose up -d postgres redis orchestrator controller agent-launcher admin-backend admin-frontend

core-down:
	@echo "Stopping core platform services..."
	docker-compose down postgres redis orchestrator controller agent-launcher admin-backend admin-frontend

restart: down up

restart-service:
	@read -p "Service name: " svc; \
	docker-compose restart $$svc

# =============================================================================
# Logs & Debugging
# =============================================================================

logs:
	docker-compose logs -f

logs-admin-backend:
	docker-compose logs -f admin-backend

logs-admin-frontend:
	docker-compose logs -f admin-frontend

logs-controller:
	docker-compose logs -f controller

logs-agent-launcher:
	docker-compose logs -f agent-launcher

logs-orchestrator:
	docker-compose logs -f orchestrator

logs-postgres:
	docker-compose logs -f postgres

logs-redis:
	docker-compose logs -f redis

# =============================================================================
# Shell Access
# =============================================================================

shell-admin-backend:
	docker-compose exec admin-backend /bin/bash

shell-controller:
	docker-compose exec controller /bin/bash

shell-agent-launcher:
	docker-compose exec agent-launcher /bin/bash

shell-orchestrator:
	docker-compose exec orchestrator /bin/bash

shell-postgres:
	docker-compose exec postgres psql -U mase -d mase

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
	@echo "Stopping and removing core containers..."
	docker-compose down -v
	@echo "Core containers removed. Per-run containers may still be active."
	@echo "Run 'make clean-runs' to stop all runs."

clean-runs:
	@echo "Stopping all per-run containers..."
	@docker ps -q -f "label=mase.run_id" | xargs -r docker stop
	@docker ps -aq -f "label=mase.run_id" | xargs -r docker rm
	@echo "All per-run containers stopped."

clean-all: clean clean-runs
	@echo "Removing images..."
	docker-compose down --rmi all
	docker system prune -f

# =============================================================================
# Utilities
# =============================================================================

ps:
	docker-compose ps

status:
	@echo "MASE Platform Status:"
	@docker-compose ps
	@echo ""
	@echo "Health Checks:"
	@curl -s http://localhost:8001/health 2>/dev/null && echo " ✓ admin-backend" || echo " ✗ admin-backend"
	@curl -s http://localhost:8002/health 2>/dev/null && echo " ✓ controller" || echo " ✗ controller"
	@curl -s http://localhost:8004/health 2>/dev/null && echo " ✓ agent-launcher" || echo " ✗ agent-launcher"
	@curl -s http://localhost:8006/health 2>/dev/null && echo " ✓ orchestrator" || echo " ✗ orchestrator"

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
