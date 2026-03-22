#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"

if [[ -z "${ADMIN_BACKEND_PORT:-}" ]] || [[ -z "${CONTROLLER_PORT:-}" ]]; then
  eval "$(bash "$repo_root/scripts/runtime_env.sh" "$repo_root")"
fi

RUN_ID="${1:-}"
if [ -z "$RUN_ID" ]; then
  echo "usage: $0 <run_id>" >&2
  exit 2
fi

ADMIN_BASE="${ADMIN_BASE:-http://localhost:${ADMIN_BACKEND_PORT}/api}"
CTRL_BASE="${CTRL_BASE:-http://localhost:${CONTROLLER_PORT}/api}"
PAUSE_SETTLE_SECONDS="${PAUSE_SETTLE_SECONDS:-3}"
PAUSE_TAIL_SECONDS="${PAUSE_TAIL_SECONDS:-12}"
RESUME_SETTLE_SECONDS="${RESUME_SETTLE_SECONDS:-10}"
STOP_SETTLE_SECONDS="${STOP_SETTLE_SECONDS:-5}"
PAUSE_ACTION_TAIL_TOLERANCE="${PAUSE_ACTION_TAIL_TOLERANCE:-1}"

failures=0
notes=()

run_curl() {
  local url="$1"
  curl -sS "$url"
}

post_curl() {
  local url="$1"
  curl -sS -X POST "$url"
}

json_field() {
  local json_input="$1"
  local jq_expr="$2"
  printf '%s' "$json_input" | jq -r "$jq_expr"
}

get_run_status() {
  run_curl "$CTRL_BASE/v1/runs/$RUN_ID" | jq -r '.status // "unknown"'
}

get_scheduler_state_ctrl() {
  run_curl "$CTRL_BASE/v1/runs/$RUN_ID/scheduler/status" | jq -r '.state // "unknown"'
}

get_scheduler_state_admin() {
  run_curl "$ADMIN_BASE/v1/runs/$RUN_ID/scheduler/status" | jq -r '.state // "unknown"'
}

get_action_attempt_count() {
  run_curl "$CTRL_BASE/v1/events/runs/$RUN_ID/count" | jq -r '.event_types.action_attempt // 0'
}

count_running_containers_for_run() {
  local names
  names="$(docker ps --format '{{.Names}}' | grep "$RUN_ID" || true)"
  if [ -z "$names" ]; then
    echo "0"
    return
  fi
  printf '%s\n' "$names" | wc -l | awk '{print $1}'
}

count_running_agent_like_containers_for_run() {
  local names
  names="$(
    docker ps --format '{{.Names}}' \
      | grep "$RUN_ID" \
      | grep -E 'agent|inst-ai|institutional' \
      || true
  )"
  if [ -z "$names" ]; then
    echo "0"
    return
  fi
  printf '%s\n' "$names" | wc -l | awk '{print $1}'
}

count_running_environment_like_containers_for_run() {
  local names
  names="$(
    docker ps --format '{{.Names}} {{.Labels}}' \
      | grep "$RUN_ID" \
      | grep -E 'mase.kind=environment-(backend|frontend)' \
      || true
  )"
  if [ -z "$names" ]; then
    echo "0"
    return
  fi
  printf '%s\n' "$names" | wc -l | awk '{print $1}'
}

pre_run_status="$(get_run_status)"
pre_sched_ctrl="$(get_scheduler_state_ctrl)"
pre_sched_admin="$(get_scheduler_state_admin)"
pre_action_count="$(get_action_attempt_count)"

if [ "$pre_run_status" != "running" ]; then
  failures=$((failures + 1))
  notes+=("expected initial run status 'running', got '$pre_run_status'")
fi
if [ "$pre_sched_ctrl" != "running" ] || [ "$pre_sched_admin" != "running" ]; then
  failures=$((failures + 1))
  notes+=("expected initial scheduler state running (ctrl=$pre_sched_ctrl admin=$pre_sched_admin)")
fi

pause_response="$(post_curl "$ADMIN_BASE/v1/runs/$RUN_ID/pause")"
sleep "$PAUSE_SETTLE_SECONDS"

paused_run_status="$(get_run_status)"
paused_sched_ctrl="$(get_scheduler_state_ctrl)"
paused_sched_admin="$(get_scheduler_state_admin)"
paused_action_count_settle="$(get_action_attempt_count)"

sleep "$PAUSE_TAIL_SECONDS"
paused_action_count_tail="$(get_action_attempt_count)"
pause_delta_tail=$((paused_action_count_tail - paused_action_count_settle))

if [ "$paused_run_status" != "paused" ]; then
  failures=$((failures + 1))
  notes+=("expected paused run status 'paused', got '$paused_run_status'")
fi
if [ "$paused_sched_ctrl" != "paused" ] || [ "$paused_sched_admin" != "paused" ]; then
  failures=$((failures + 1))
  notes+=("expected paused scheduler state paused (ctrl=$paused_sched_ctrl admin=$paused_sched_admin)")
fi
if [ "$pause_delta_tail" -gt "$PAUSE_ACTION_TAIL_TOLERANCE" ]; then
  failures=$((failures + 1))
  notes+=("action_attempt delta during pause tail window exceeded tolerance: delta=$pause_delta_tail tolerance=$PAUSE_ACTION_TAIL_TOLERANCE")
fi

resume_response="$(post_curl "$ADMIN_BASE/v1/runs/$RUN_ID/resume")"
sleep "$RESUME_SETTLE_SECONDS"

resumed_run_status="$(get_run_status)"
resumed_sched_ctrl="$(get_scheduler_state_ctrl)"
resumed_sched_admin="$(get_scheduler_state_admin)"
resumed_action_count="$(get_action_attempt_count)"
resume_delta=$((resumed_action_count - paused_action_count_tail))

if [ "$resumed_run_status" != "running" ]; then
  failures=$((failures + 1))
  notes+=("expected resumed run status 'running', got '$resumed_run_status'")
fi
if [ "$resumed_sched_ctrl" != "running" ] || [ "$resumed_sched_admin" != "running" ]; then
  failures=$((failures + 1))
  notes+=("expected resumed scheduler state running (ctrl=$resumed_sched_ctrl admin=$resumed_sched_admin)")
fi
if [ "$resume_delta" -lt 0 ]; then
  failures=$((failures + 1))
  notes+=("unexpected negative action delta after resume: delta=$resume_delta")
fi

stop_response="$(post_curl "$ADMIN_BASE/v1/runs/$RUN_ID/stop")"
sleep "$STOP_SETTLE_SECONDS"

stopped_run_status="$(get_run_status)"
stopped_sched_ctrl="$(get_scheduler_state_ctrl)"
stopped_sched_admin="$(get_scheduler_state_admin)"

running_containers_after_stop="$(count_running_containers_for_run)"
agent_like_running_after_stop="$(count_running_agent_like_containers_for_run)"
environment_like_running_after_stop="$(count_running_environment_like_containers_for_run)"

if [ "$stopped_run_status" != "cancelled" ] && [ "$stopped_run_status" != "stopped" ] && [ "$stopped_run_status" != "completed" ]; then
  failures=$((failures + 1))
  notes+=("unexpected run status after stop: '$stopped_run_status'")
fi
if [ "$stopped_sched_ctrl" != "stopped" ] || [ "$stopped_sched_admin" != "stopped" ]; then
  failures=$((failures + 1))
  notes+=("expected stopped scheduler fallback state (ctrl=$stopped_sched_ctrl admin=$stopped_sched_admin)")
fi
if [ "$running_containers_after_stop" -ne 0 ]; then
  failures=$((failures + 1))
  notes+=("expected zero run-scoped containers after stop, found $running_containers_after_stop")
fi
if [ "$agent_like_running_after_stop" -ne 0 ]; then
  failures=$((failures + 1))
  notes+=("expected zero agent/institutional containers after stop, found $agent_like_running_after_stop")
fi
if [ "$environment_like_running_after_stop" -ne 0 ]; then
  failures=$((failures + 1))
  notes+=("expected zero environment containers after stop, found $environment_like_running_after_stop")
fi

jq -n \
  --arg run_id "$RUN_ID" \
  --arg pre_run_status "$pre_run_status" \
  --arg pre_sched_ctrl "$pre_sched_ctrl" \
  --arg pre_sched_admin "$pre_sched_admin" \
  --argjson pre_action_count "$pre_action_count" \
  --arg paused_run_status "$paused_run_status" \
  --arg paused_sched_ctrl "$paused_sched_ctrl" \
  --arg paused_sched_admin "$paused_sched_admin" \
  --argjson paused_action_count_settle "$paused_action_count_settle" \
  --argjson paused_action_count_tail "$paused_action_count_tail" \
  --argjson pause_delta_tail "$pause_delta_tail" \
  --arg resumed_run_status "$resumed_run_status" \
  --arg resumed_sched_ctrl "$resumed_sched_ctrl" \
  --arg resumed_sched_admin "$resumed_sched_admin" \
  --argjson resumed_action_count "$resumed_action_count" \
  --argjson resume_delta "$resume_delta" \
  --arg stopped_run_status "$stopped_run_status" \
  --arg stopped_sched_ctrl "$stopped_sched_ctrl" \
  --arg stopped_sched_admin "$stopped_sched_admin" \
  --argjson running_containers_after_stop "$running_containers_after_stop" \
  --argjson agent_like_running_after_stop "$agent_like_running_after_stop" \
  --argjson environment_like_running_after_stop "$environment_like_running_after_stop" \
  --argjson failures "$failures" \
  --argjson notes "$(printf '%s\n' "${notes[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
  --argjson pause_response "$pause_response" \
  --argjson resume_response "$resume_response" \
  --argjson stop_response "$stop_response" \
  '{
    run_id: $run_id,
    checks: {
      initial: {
        run_status: $pre_run_status,
        scheduler_ctrl: $pre_sched_ctrl,
        scheduler_admin: $pre_sched_admin,
        action_attempt_count: $pre_action_count
      },
      paused: {
        run_status: $paused_run_status,
        scheduler_ctrl: $paused_sched_ctrl,
        scheduler_admin: $paused_sched_admin,
        action_attempt_count_settle: $paused_action_count_settle,
        action_attempt_count_tail: $paused_action_count_tail,
        action_attempt_delta_tail: $pause_delta_tail
      },
      resumed: {
        run_status: $resumed_run_status,
        scheduler_ctrl: $resumed_sched_ctrl,
        scheduler_admin: $resumed_sched_admin,
        action_attempt_count: $resumed_action_count,
        action_attempt_delta: $resume_delta
      },
      stopped: {
        run_status: $stopped_run_status,
        scheduler_ctrl: $stopped_sched_ctrl,
        scheduler_admin: $stopped_sched_admin,
        running_containers: $running_containers_after_stop,
        agent_like_running_containers: $agent_like_running_after_stop,
        environment_like_running_containers: $environment_like_running_after_stop
      }
    },
    lifecycle_responses: {
      pause: $pause_response,
      resume: $resume_response,
      stop: $stop_response
    },
    failures: $failures,
    notes: $notes,
    pass: ($failures == 0)
  }'

if [ "$failures" -gt 0 ]; then
  exit 1
fi
