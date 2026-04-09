#!/usr/bin/env bash
set -euo pipefail

format="export"
if [[ "${1:-}" == "--dotenv" ]]; then
  format="dotenv"
  shift
fi

repo_root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
repo_root="$(cd "$repo_root" && pwd -P)"

namespace="$(
  printf '%s' "$(basename "$repo_root")" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
)"
namespace="${namespace:-mase}"

emit() {
  local key="$1"
  local value="$2"

  if [[ "$format" == "dotenv" ]]; then
    printf '%s=%s\n' "$key" "$value"
  else
    printf 'export %s=%q\n' "$key" "$value"
  fi
}

resolve_optional_from_dotenv() {
  local key="$1"
  local dotenv_path="$2"
  local value=""

  if [[ ! -f "$dotenv_path" ]]; then
    return 1
  fi

  value="$(
    DOTENV_PATH="$dotenv_path" DOTENV_KEY="$key" python3 - <<'PY'
import os
from pathlib import Path

dotenv_path = Path(os.environ["DOTENV_PATH"])
target = os.environ["DOTENV_KEY"]
value = ""
for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, raw_value = line.split("=", 1)
    if key.strip() != target:
        continue
    parsed = raw_value.strip()
    if (parsed.startswith('"') and parsed.endswith('"')) or (parsed.startswith("'") and parsed.endswith("'")):
        parsed = parsed[1:-1]
    value = parsed
    break
print(value, end="")
PY
  )"
  [[ -n "$value" ]] || return 1
  printf '%s' "$value"
}

resolve_dotenv_path() {
  local root="$1"
  local candidate="$root/.env"
  local common_dir=""
  local shared_root=""

  if [[ -f "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  common_dir="$(git -C "$root" rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$common_dir" ]]; then
    if [[ "$common_dir" != /* ]]; then
      common_dir="$(cd "$root" && cd "$common_dir" && pwd -P)"
    fi
    shared_root="$(cd "$common_dir/.." 2>/dev/null && pwd -P || true)"
    if [[ -n "$shared_root" && -f "$shared_root/.env" ]]; then
      printf '%s' "$shared_root/.env"
      return 0
    fi
  fi

  printf '%s' "$candidate"
}

emit "COMPOSE_PROJECT_NAME" "$namespace"
emit "HOST_PROJECT_ROOT" "$repo_root"
emit "MASE_NETWORK_NAME" "${namespace}-network"
emit "ADMIN_FRONTEND_PORT" "${ADMIN_FRONTEND_PORT:-3016}"
emit "ADMIN_BACKEND_PORT" "${ADMIN_BACKEND_PORT:-8001}"
emit "CONTROLLER_PORT" "${CONTROLLER_PORT:-8002}"
emit "AGENT_LAUNCHER_PORT" "${AGENT_LAUNCHER_PORT:-8004}"
emit "ORCHESTRATOR_PORT" "${ORCHESTRATOR_PORT:-8006}"
emit "POSTGRES_PORT" "${POSTGRES_PORT:-5433}"
emit "REDIS_PORT" "${REDIS_PORT:-6380}"
emit "PROMETHEUS_PORT" "${PROMETHEUS_PORT:-9090}"
emit "GRAFANA_PORT" "${GRAFANA_PORT:-3001}"
emit "AGENT_WORKER_IMAGE" "${AGENT_WORKER_IMAGE:-mase-agent-launcher:${namespace}}"
emit "MASE_IMAGE_NAMESPACE" "${MASE_IMAGE_NAMESPACE:-$namespace}"

dotenv_path="$(resolve_dotenv_path "$repo_root")"
openrouter_api_key="$(
  resolve_optional_from_dotenv "OPENROUTER_API_KEY" "$dotenv_path" \
    || resolve_optional_from_dotenv "AGENT_LAUNCHER_OPENROUTER_API_KEY" "$dotenv_path" \
    || resolve_optional_from_dotenv "SIM_CTRL_OPENROUTER_API_KEY" "$dotenv_path" \
    || true
)"
agent_launcher_openrouter_api_key="$(
  resolve_optional_from_dotenv "AGENT_LAUNCHER_OPENROUTER_API_KEY" "$dotenv_path" \
    || printf '%s' "$openrouter_api_key"
)"
sim_ctrl_openrouter_api_key="$(
  resolve_optional_from_dotenv "SIM_CTRL_OPENROUTER_API_KEY" "$dotenv_path" \
    || printf '%s' "$openrouter_api_key"
)"

if [[ -n "$openrouter_api_key" ]]; then
  emit "OPENROUTER_API_KEY" "$openrouter_api_key"
fi
if [[ -n "$agent_launcher_openrouter_api_key" ]]; then
  emit "AGENT_LAUNCHER_OPENROUTER_API_KEY" "$agent_launcher_openrouter_api_key"
fi
if [[ -n "$sim_ctrl_openrouter_api_key" ]]; then
  emit "SIM_CTRL_OPENROUTER_API_KEY" "$sim_ctrl_openrouter_api_key"
fi
