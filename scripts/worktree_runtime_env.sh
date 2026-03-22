#!/usr/bin/env bash
set -euo pipefail

format="export"
if [[ "${1:-}" == "--dotenv" ]]; then
  format="dotenv"
  shift
fi

worktree_root="${1:-$PWD}"
worktree_root="$(cd "$worktree_root" && pwd -P)"

namespace="$(
  printf '%s' "$(basename "$worktree_root")" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
)"
namespace="${namespace:-mase}"

if [[ -n "${WT_PORT_OFFSET:-}" ]]; then
  offset="${WT_PORT_OFFSET}"
else
  offset="$(printf '%s' "$worktree_root" | cksum | awk '{print $1 % 400}')"
fi

emit() {
  local key="$1"
  local value="$2"

  if [[ "$format" == "dotenv" ]]; then
    printf '%s=%s\n' "$key" "$value"
  else
    printf 'export %s=%q\n' "$key" "$value"
  fi
}

resolve_common_repo_root() {
  local worktree_path="$1"
  local common_git_dir=""
  local resolved_common_dir=""

  if ! common_git_dir="$(git -C "$worktree_path" rev-parse --git-common-dir 2>/dev/null)"; then
    return 0
  fi
  if [[ -z "$common_git_dir" ]]; then
    return 0
  fi
  if [[ "$common_git_dir" = /* ]]; then
    resolved_common_dir="$common_git_dir"
  else
    resolved_common_dir="$worktree_path/$common_git_dir"
  fi
  (cd "$resolved_common_dir/.." && pwd -P) 2>/dev/null || true
}

resolve_optional_from_dotenv_candidates() {
  local key="$1"
  shift
  local dotenv_path=""
  local value=""

  for dotenv_path in "$@"; do
    if [[ -z "${dotenv_path:-}" ]] || [[ ! -f "$dotenv_path" ]]; then
      continue
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
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done
  return 1
}

emit "COMPOSE_PROJECT_NAME" "$namespace"
emit "HOST_PROJECT_ROOT" "$worktree_root"
emit "MASE_NETWORK_NAME" "${namespace}-network"
emit "ADMIN_FRONTEND_PORT" "$((3016 + offset))"
emit "ADMIN_BACKEND_PORT" "$((8001 + offset))"
emit "CONTROLLER_PORT" "$((8002 + offset))"
emit "AGENT_LAUNCHER_PORT" "$((8004 + offset))"
emit "ORCHESTRATOR_PORT" "$((8006 + offset))"
emit "POSTGRES_PORT" "$((5433 + offset))"
emit "REDIS_PORT" "$((6380 + offset))"
emit "PROMETHEUS_PORT" "$((9090 + offset))"
emit "GRAFANA_PORT" "$((3001 + offset))"
emit "AGENT_WORKER_IMAGE" "mase-agent-launcher:${namespace}"
emit "MASE_IMAGE_NAMESPACE" "$namespace"

dotenv_path="$worktree_root/.env"
common_repo_root="$(resolve_common_repo_root "$worktree_root")"
common_dotenv_path=""
if [[ -n "${common_repo_root:-}" ]]; then
  common_dotenv_path="$common_repo_root/.env"
fi

openrouter_api_key="$(
  resolve_optional_from_dotenv_candidates "OPENROUTER_API_KEY" "$dotenv_path" "$common_dotenv_path" \
    || resolve_optional_from_dotenv_candidates "AGENT_LAUNCHER_OPENROUTER_API_KEY" "$dotenv_path" "$common_dotenv_path" \
    || resolve_optional_from_dotenv_candidates "SIM_CTRL_OPENROUTER_API_KEY" "$dotenv_path" "$common_dotenv_path" \
    || true
)"
agent_launcher_openrouter_api_key="$(
  resolve_optional_from_dotenv_candidates "AGENT_LAUNCHER_OPENROUTER_API_KEY" "$dotenv_path" "$common_dotenv_path" \
    || printf '%s' "$openrouter_api_key"
)"
sim_ctrl_openrouter_api_key="$(
  resolve_optional_from_dotenv_candidates "SIM_CTRL_OPENROUTER_API_KEY" "$dotenv_path" "$common_dotenv_path" \
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
