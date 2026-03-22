#!/bin/bash
# =============================================================================
# Setup script for development volumes
# Creates host directories with proper permissions for Docker bind mounts
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_DIR/data"

echo "=== MASE Development Volume Setup ==="
echo ""

# Create data directories
echo "Creating shared data directories..."
mkdir -p "$DATA_DIR"/{runs,agents,environments,orchestrator,admin,logs}

# Create run subdirectories
mkdir -p "$DATA_DIR/runs"/{metrics,state}

# Create agent filesystem structure
mkdir -p "$DATA_DIR/agents"

# Create log subdirectories
mkdir -p "$DATA_DIR/logs"/{controller,agent-launcher}

echo "Directories created:"
echo "  $DATA_DIR/runs           - Run DB, metrics, state"
echo "  $DATA_DIR/agents         - Agent filesystems"
echo "  $DATA_DIR/environments   - Environment-local data"
echo "  $DATA_DIR/orchestrator   - Orchestrator state"
echo "  $DATA_DIR/admin          - Admin backend data"
echo "  $DATA_DIR/logs           - Service logs"
echo ""

# Set permissions (Docker containers run as root, but we want host user access)
echo "Setting permissions..."
chmod -R 777 "$DATA_DIR"

echo "Permissions set to 777 for Docker compatibility"
echo ""

# Create .gitignore for data directory
cat > "$DATA_DIR/.gitignore" << 'EOF'
# Ignore all data files in git
*
!.gitignore
EOF

echo "Created $DATA_DIR/.gitignore"
echo ""

echo "=== Setup Complete ==="
echo ""
echo "To use shared volumes:"
echo "  docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d"
echo ""
echo "To inspect data:"
echo "  ls -la $DATA_DIR/runs/"
echo "  sqlite3 $DATA_DIR/runs/run_controller.db"
echo "  ls -la $DATA_DIR/agents/"
echo ""
