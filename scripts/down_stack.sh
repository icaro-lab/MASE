#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
compose_file="$repo_root/docker-compose.yml"
compose_cmd=(docker compose -f "$compose_file")

include_runs=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-runs)
      include_runs=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/down_stack.sh [--include-runs]" >&2
      exit 2
      ;;
  esac
done

eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"

if [[ "$include_runs" == "1" ]]; then
  run_projects=()
  while IFS= read -r project; do
    [[ -n "$project" ]] || continue
    run_projects+=("$project")
  done < <(
    docker ps -a --format '{{.Label "com.docker.compose.project"}}' \
      | awk -v prefix="${COMPOSE_PROJECT_NAME}-run-" 'index($0, prefix) == 1 {print $0}' \
      | sort -u
  )

  if [[ ${#run_projects[@]} -gt 0 ]]; then
    for project in "${run_projects[@]}"; do
      [[ -n "$project" ]] || continue
      docker compose -p "$project" down -v || true
    done
  fi
fi

"${compose_cmd[@]}" down -v

cat <<EOF
Platform stack stopped
  compose project: ${COMPOSE_PROJECT_NAME}
  include runs:    ${include_runs}
EOF
