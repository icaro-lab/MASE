#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:3016}"
REPORT_PATH="${REPORT_PATH:-logs/$(date +%Y%m%d%H%M)__frontend-route-matrix-shadcn-3016.txt}"

BASE_URL="$BASE_URL" REPORT_PATH="$REPORT_PATH" bash scripts/verify_frontend_route_matrix.sh
