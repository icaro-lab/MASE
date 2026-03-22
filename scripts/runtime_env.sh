#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ $# -eq 0 ]]; then
  exec bash "$repo_root/scripts/worktree_runtime_env.sh" "$repo_root"
fi

exec bash "$repo_root/scripts/worktree_runtime_env.sh" "$@"
