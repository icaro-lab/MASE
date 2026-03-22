#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
compose_file="$repo_root/docker-compose.yml"
compose_cmd=(docker compose -f "$compose_file")

build_runtime_images=1
verify_stack=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-runtime-images)
      build_runtime_images=0
      shift
      ;;
    --no-verify)
      verify_stack=0
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/up_stack.sh [--skip-runtime-images] [--no-verify]" >&2
      exit 2
      ;;
  esac
done

eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"

if [[ "$build_runtime_images" == "1" ]]; then
  bash "$repo_root/scripts/build_images.sh"
fi

wait_for_postgres() {
  local attempt

  for attempt in $(seq 1 60); do
    if "${compose_cmd[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-mase}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for postgres readiness" >&2
  return 1
}

ensure_platform_databases() {
  local database

  for database in mase_admin mase_runs; do
    if "${compose_cmd[@]}" exec -T postgres psql \
      -U "${POSTGRES_USER:-mase}" \
      -d postgres \
      -tAc "SELECT 1 FROM pg_database WHERE datname = '${database}'" | grep -q '^1$'; then
      continue
    fi
    "${compose_cmd[@]}" exec -T postgres createdb \
      -U "${POSTGRES_USER:-mase}" \
      "$database" >/dev/null
  done
}

"${compose_cmd[@]}" up -d postgres redis
wait_for_postgres
ensure_platform_databases
"${compose_cmd[@]}" up -d --build orchestrator controller agent-launcher admin-backend admin-frontend

wait_for_http() {
  local name="$1"
  local url="$2"
  local attempt

  for attempt in $(seq 1 60); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for ${name} at ${url}" >&2
  return 1
}

if [[ "$verify_stack" == "1" ]]; then
  wait_for_http "controller" "http://localhost:${CONTROLLER_PORT}/health"
  wait_for_http "agent-launcher" "http://localhost:${AGENT_LAUNCHER_PORT}/health"
  wait_for_http "admin-backend" "http://localhost:${ADMIN_BACKEND_PORT}/api/health"
  wait_for_http "orchestrator" "http://localhost:${ORCHESTRATOR_PORT}/health"
  bash "$repo_root/scripts/verify_platform.sh"
fi

cat <<EOF
Platform stack ready
  compose project: ${COMPOSE_PROJECT_NAME}
  network:         ${MASE_NETWORK_NAME}
  admin frontend:  http://localhost:${ADMIN_FRONTEND_PORT}
  admin backend:   http://localhost:${ADMIN_BACKEND_PORT}
  controller:      http://localhost:${CONTROLLER_PORT}
  agent launcher:  http://localhost:${AGENT_LAUNCHER_PORT}
  orchestrator:    http://localhost:${ORCHESTRATOR_PORT}
EOF
