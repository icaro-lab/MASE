#!/bin/bash
# =============================================================================
# MASE Setup Script
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║        MASE (Multi-Agent Simulation Environment)              ║"
echo "║                    Setup Script                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker is not installed. Please install Docker first.${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}Docker Compose is not installed. Please install Docker Compose first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites met${NC}"

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo -e "${YELLOW}Creating .env file from template...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file${NC}"
    echo -e "${YELLOW}Please edit .env with your configuration${NC}"
else
    echo -e "${GREEN}✓ .env file already exists${NC}"
fi

# Create necessary directories
echo -e "${YELLOW}Creating necessary directories...${NC}"
mkdir -p logs data/certs config/grafana/dashboards config/grafana/datasources
echo -e "${GREEN}✓ Directories created${NC}"

# Build Docker images
echo -e "${YELLOW}Building Docker images...${NC}"
docker-compose build
echo -e "${GREEN}✓ Docker images built${NC}"

# Initialize database
echo -e "${YELLOW}Initializing database...${NC}"
./scripts/init_db.sh
echo -e "${GREEN}✓ Database initialized${NC}"

# Networks are created by docker compose using .env values.
echo -e "${GREEN}✓ Docker networks will be created by docker compose${NC}"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete!                            ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}To start MASE:${NC}"
echo "  docker-compose up -d"
echo ""
echo -e "${BLUE}To access the services:${NC}"
echo "  • Admin Dashboard:    http://localhost:${ADMIN_FRONTEND_PORT:-3016}"
echo "  • Admin API Docs:     http://localhost:8001/docs"
echo "  • Controller API:     http://localhost:8002/docs"
echo "  • Agent Launcher:     http://localhost:8004/docs"
echo "  • Orchestrator:       http://localhost:8006/health"
echo ""
echo -e "${BLUE}To check service health:${NC}"
echo "  curl http://localhost:8001/api/health"
echo "  curl http://localhost:8002/health"
echo "  curl http://localhost:8004/health"
echo "  curl http://localhost:8006/health"
echo ""
echo -e "${BLUE}To view logs:${NC}"
echo "  docker-compose logs -f"
echo ""
echo -e "${YELLOW}Happy simulating! 🚀${NC}"
