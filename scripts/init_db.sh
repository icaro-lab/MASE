#!/bin/bash
# =============================================================================
# MASE Database Initialization Script (for Docker PostgreSQL)
# This script runs inside the PostgreSQL container on first startup
# =============================================================================

set -e

# Create databases if they don't exist
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    -- Create databases for MASE services
    SELECT 'CREATE DATABASE mase_admin'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mase_admin')\gexec
    
    SELECT 'CREATE DATABASE mase_runs'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mase_runs')\gexec
    
    SELECT 'CREATE DATABASE mase_ledger'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mase_ledger')\gexec
EOSQL

echo "MASE databases initialized successfully!"
