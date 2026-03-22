#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"

eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"

controller_url="http://localhost:${CONTROLLER_PORT}"
api_key="${OPENROUTER_API_KEY:-dummy}"

launch_payload="$(python3 - <<'PY'
import json
print(json.dumps({
    "environment_id": "hello-world",
    "api_key": __import__("os").environ.get("OPENROUTER_API_KEY", "dummy"),
}))
PY
)"

launch_response="$(
  curl -sf -X POST "${controller_url}/api/v1/runs" \
    -H "Content-Type: application/json" \
    -d "${launch_payload}"
)"

run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$launch_response")"
frontend_url="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("frontend_url") or "")' <<<"$launch_response")"

if [[ -z "$run_id" || -z "$frontend_url" ]]; then
  echo "hello-world smoke launch did not return run_id/frontend_url" >&2
  echo "$launch_response" >&2
  exit 1
fi

wait_for_json() {
  local url="$1"
  local attempt

  for attempt in $(seq 1 90); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for ${url}" >&2
  return 1
}

wait_for_json "${controller_url}/api/v1/runs/${run_id}"
wait_for_json "${frontend_url}/api/v1/board"

board_payload="$(curl -sf "${frontend_url}/api/v1/board")"
title="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("title") or "")' <<<"$board_payload")"

if [[ "$title" != "Hello World Whiteboard" ]]; then
  echo "Unexpected hello-world board title: ${title}" >&2
  echo "$board_payload" >&2
  exit 1
fi

echo "hello-world smoke ok"
echo "  run_id:       ${run_id}"
echo "  frontend_url: ${frontend_url}"
