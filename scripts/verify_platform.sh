#!/usr/bin/env bash
# Comprehensive verification script for the canonical local platform stack.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
compose_file="$repo_root/docker-compose.yml"
compose_cmd="docker compose -f \"$compose_file\""

if [[ -z "${ADMIN_FRONTEND_PORT:-}" ]] || [[ -z "${ADMIN_BACKEND_PORT:-}" ]] || [[ -z "${CONTROLLER_PORT:-}" ]] || [[ -z "${AGENT_LAUNCHER_PORT:-}" ]]; then
    eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"
fi

echo "=========================================="
echo "MASE Platform Verification Script"
echo "Mode: canonical local stack"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track results
TESTS_PASSED=0
TESTS_FAILED=0
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
VERIFY_PLATFORM_STRICT_RUNTIME_HARNESS="${VERIFY_PLATFORM_STRICT_RUNTIME_HARNESS:-0}"
ADMIN_BACKEND_PORT="${ADMIN_BACKEND_PORT:-8001}"
CONTROLLER_PORT="${CONTROLLER_PORT:-8002}"
AGENT_LAUNCHER_PORT="${AGENT_LAUNCHER_PORT:-8004}"
ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8006}"
ADMIN_FRONTEND_PORT="${ADMIN_FRONTEND_PORT:-3016}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://localhost:${ADMIN_FRONTEND_PORT}}"

resolve_verify_environment_id() {
    if [[ -n "${VERIFY_ENVIRONMENT_ID:-}" ]]; then
        printf '%s\n' "$VERIFY_ENVIRONMENT_ID"
        return 0
    fi

    python3 - <<'PY'
from pathlib import Path

root = Path("environments")
for child in sorted(root.iterdir()) if root.exists() else []:
    if not child.is_dir():
        continue
    if child.name.startswith(".") or child.name.startswith("_"):
        continue
    if (child / "environment.yaml").is_file():
        print(child.name)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

run_project_pattern() {
    local namespace="${COMPOSE_PROJECT_NAME:-mase}"
    printf 'com.docker.compose.project=%s-run-' "$namespace"
}

find_run_environment_backend_container() {
    local run_containers
    local container

    run_containers="$(
        docker ps --format '{{.Names}} {{.Labels}}' \
            | grep -F "$(run_project_pattern)" \
            || true
    )"

    if [ -z "$run_containers" ]; then
        return 0
    fi

    printf '%s\n' "$run_containers" \
        | grep -F 'mase.kind=environment-backend' \
        | head -1 \
        | awk '{print $1}' \
        || true
}

echo "Compose project: ${COMPOSE_PROJECT_NAME}"

# Function to test and report
test_step() {
    local name=$1
    local command=$2
    echo -n "Testing: $name... "
    if eval "$command" > /tmp/test_output.txt 2>&1; then
        echo -e "${GREEN}✓ PASS${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}"
        echo "  Output: $(cat /tmp/test_output.txt | head -2)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

echo "1. Container Health Checks"
echo "--------------------------"
test_step "PostgreSQL is running" "${compose_cmd} ps -q postgres | grep -q ."
test_step "Redis is running" "${compose_cmd} ps -q redis | grep -q ."
test_step "Controller is running" "${compose_cmd} ps -q controller | grep -q ."
test_step "Agent Launcher is running" "${compose_cmd} ps -q agent-launcher | grep -q ."
test_step "Admin Backend is running" "${compose_cmd} ps -q admin-backend | grep -q ."
test_step "Admin Frontend is running" "${compose_cmd} ps -q admin-frontend | grep -q ."
test_step "Orchestrator is running" "${compose_cmd} ps -q orchestrator | grep -q ."

echo ""
echo "2. Service Health Endpoints"
echo "--------------------------"
test_step "Controller health" "curl -sf http://localhost:${CONTROLLER_PORT}/health | grep -q 'healthy'"
test_step "Agent Launcher health" "curl -sf http://localhost:${AGENT_LAUNCHER_PORT}/health | grep -q 'healthy'"
test_step "Admin Backend health" "curl -sf http://localhost:${ADMIN_BACKEND_PORT}/api/health | grep -q 'healthy'"
test_step "Orchestrator health" "curl -sf http://localhost:${ORCHESTRATOR_PORT}/health | grep -q 'healthy'"

echo ""
echo "3. Run Baseline Enforcement"
echo "---------------------------"
test_step "No deprecated extra-service container is running for this worktree" "! docker ps --format '{{.Names}} {{.Labels}}' | grep -F \"$(run_project_pattern)\" | grep -Eq '(inst-ai|compass)'"

echo ""
echo "4. Environment Contract"
echo "-----------------------"
VERIFY_ENVIRONMENT_ID="${VERIFY_ENVIRONMENT_ID:-$(resolve_verify_environment_id)}"
VERIFY_ENVIRONMENT_DIR="environments/${VERIFY_ENVIRONMENT_ID}"
test_step "Environment manifest validates (${VERIFY_ENVIRONMENT_ID})" "curl -sf -X POST http://localhost:${CONTROLLER_PORT}/api/v1/environments/${VERIFY_ENVIRONMENT_ID}/validate | grep -q '\"valid\":true'"
ENV_CONTAINER="$(find_run_environment_backend_container)"
if [ -n "$ENV_CONTAINER" ]; then
    test_step "Environment /health responds (${ENV_CONTAINER})" "docker exec $ENV_CONTAINER curl -sf http://localhost:8000/health | grep -q 'healthy'"
    test_step "Environment /contract exposes contract version (${ENV_CONTAINER})" "docker exec $ENV_CONTAINER curl -sf http://localhost:8000/contract | grep -q 'contract_version'"
    test_step "Environment /skill.md responds (${ENV_CONTAINER})" "docker exec $ENV_CONTAINER curl -sf http://localhost:8000/skill.md >/dev/null"
else
    echo -e "${YELLOW}⚠ SKIP${NC} Live environment endpoint checks (no active run environment service)"
fi

echo ""
echo "5. Public Runtime And Environment Surface"
echo "-----------------------------------------"
test_step "Runtime exists (openclaw)" "test -f runtimes/openclaw/runtime.yaml"
test_step "Environment exists (${VERIFY_ENVIRONMENT_ID})" "test -f ${VERIFY_ENVIRONMENT_DIR}/environment.yaml"
test_step "Environment run compose exists (${VERIFY_ENVIRONMENT_ID})" "test -f ${VERIFY_ENVIRONMENT_DIR}/compose.run.yml"
test_step "Environment skill file exists (${VERIFY_ENVIRONMENT_ID})" "test -f ${VERIFY_ENVIRONMENT_DIR}/skill.md"
test_step "Environment scaffold template exists" "test -f environments/_template/environment.yaml"
test_step "Environment scaffold script exists" "test -f scripts/scaffold_environment.sh"

echo ""
echo "6. Database Configuration"
echo "-------------------------"
test_step "mase_runs database exists" "${compose_cmd} exec -T postgres psql -U mase -c '\\l' | grep -q mase_runs"

echo ""
echo "7. Frontend Integration"
echo "-----------------------"
echo "Using frontend endpoint: ${FRONTEND_BASE_URL}"
test_step "Frontend loads (${FRONTEND_BASE_URL})" "curl -sf ${FRONTEND_BASE_URL} >/dev/null"
test_step "API proxy works (${FRONTEND_BASE_URL})" "curl -sf ${FRONTEND_BASE_URL}/api/v1/runs >/dev/null"

echo ""
echo "8. Optional Strict Runtime Harness"
echo "----------------------------------"
if [ "${VERIFY_PLATFORM_STRICT_RUNTIME_HARNESS}" = "1" ]; then
    test_step "Phase-B3 backend integration harness passes" "SC_CID=\$(${compose_cmd} ps -q controller); test -n \"\$SC_CID\" && docker cp scripts/phase_b_b3_backend_integration.py \$SC_CID:/tmp/phase_b_b3_backend_integration.py && ${compose_cmd} exec -T controller python /tmp/phase_b_b3_backend_integration.py | grep -q '\"ok\": true'"
else
    echo -e "${YELLOW}⚠ SKIP${NC} Strict runtime harness (set VERIFY_PLATFORM_STRICT_RUNTIME_HARNESS=1)"
fi

echo ""
echo "=========================================="
echo "Test Results"
echo "=========================================="
echo -e "Passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed! Platform is ready.${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed. Please review the output above.${NC}"
    exit 1
fi
