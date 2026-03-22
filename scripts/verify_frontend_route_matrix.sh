#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:3016}"
RUN_ID="${RUN_ID:-11111111-1111-1111-1111-111111111111}"
TRACE_ID="${TRACE_ID:-trace-11111111}"
ENVIRONMENT_ID="${ENVIRONMENT_ID:-}"
STAMP="${STAMP:-$(date +%Y%m%d%H%M)}"
OUT_DIR="${OUT_DIR:-tests/playwright}"
REPORT_PATH="${REPORT_PATH:-logs/${STAMP}__frontend-route-matrix.txt}"
PW_SESSION="${PW_SESSION:-route-matrix}"

mkdir -p "$OUT_DIR" "$(dirname "$REPORT_PATH")"

OPENED=0
PASS_COUNT=0
FAIL_COUNT=0
PLAYWRIGHT_CLI_CMD=()
NAV_OUTPUT=""

resolve_playwright_cli() {
  if command -v playwright-cli >/dev/null 2>&1; then
    PLAYWRIGHT_CLI_CMD=(playwright-cli)
    return
  fi

  if command -v npx >/dev/null 2>&1; then
    PLAYWRIGHT_CLI_CMD=(npx --yes --package @playwright/cli playwright-cli)
    return
  fi

  echo "Error: playwright-cli not found and npx is unavailable." >&2
  exit 1
}

pw() {
  "${PLAYWRIGHT_CLI_CMD[@]}" "$@"
}

resolve_environment_id() {
  if [[ -n "$ENVIRONMENT_ID" ]]; then
    return 0
  fi

  ENVIRONMENT_ID="$(
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
  )"
}

resolve_playwright_cli
resolve_environment_id

cleanup() {
  pw -s="$PW_SESSION" close >/dev/null 2>&1 || true
}
trap cleanup EXIT

extract_page_url() {
  sed -n 's/^[- ]*Page URL: //p' | tail -n 1
}

sanitize_label() {
  local label
  label="$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  label="${label#-}"
  label="${label%-}"
  echo "$label"
}

run_nav_command() {
  local path="$1"
  if [[ "$OPENED" -eq 0 ]]; then
    OPENED=1
    NAV_OUTPUT="$(pw -s="$PW_SESSION" open "${BASE_URL}${path}" 2>&1)"
  else
    if NAV_OUTPUT="$(pw -s="$PW_SESSION" goto "${BASE_URL}${path}" 2>&1)"; then
      return 0
    fi
    OPENED=1
    NAV_OUTPUT="$(pw -s="$PW_SESSION" open "${BASE_URL}${path}" 2>&1)"
  fi
}

run_check() {
  local label="$1"
  local input_path="$2"
  local expected_url="$3"

  local nav_output
  if ! run_nav_command "$input_path"; then
    nav_output="$NAV_OUTPUT"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    {
      echo "[FAIL] ${label}"
      echo "  input:    ${BASE_URL}${input_path}"
      echo "  expected: ${expected_url}"
      echo "  actual:   <navigation command failed>"
      echo
      echo "$nav_output"
      echo
    } >>"$REPORT_PATH"
    return
  fi
  nav_output="$NAV_OUTPUT"

  local actual_url
  actual_url="$(printf '%s\n' "$nav_output" | extract_page_url)"
  local verdict="FAIL"
  local verdict_note=""

  local shot_label
  shot_label="$(sanitize_label "$label")"
  local shot_path="${OUT_DIR}/${STAMP}__route-matrix__${shot_label}.png"
  pw -s="$PW_SESSION" screenshot --filename="$shot_path" >/dev/null 2>&1 || true

  if [[ "$actual_url" == "$expected_url" ]]; then
    verdict="PASS"
  elif [[ -n "$actual_url" && "$expected_url" != *"?"* && "$actual_url" == "$expected_url"?* ]]; then
    verdict="PASS"
    verdict_note=" (extra query params preserved)"
  elif [[ -n "$actual_url" && "$expected_url" == *"panel=1"* ]]; then
    local no_panel_url="$expected_url"
    no_panel_url="${no_panel_url/\?panel=1&/?}"
    no_panel_url="${no_panel_url/&panel=1/}"
    no_panel_url="${no_panel_url/\?panel=1/}"
    no_panel_url="${no_panel_url%\?}"
    if [[ "$actual_url" == "$no_panel_url" ]]; then
      verdict="PASS"
      verdict_note=" (panel omitted due page state)"
    fi
  fi

  if [[ "$verdict" == "PASS" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    {
      echo "[PASS] ${label}${verdict_note}"
      echo "  input:    ${BASE_URL}${input_path}"
      echo "  expected: ${expected_url}"
      echo "  actual:   ${actual_url}"
      echo "  screenshot: ${shot_path}"
      echo
    } >>"$REPORT_PATH"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    {
      echo "[FAIL] ${label}"
      echo "  input:    ${BASE_URL}${input_path}"
      echo "  expected: ${expected_url}"
      echo "  actual:   ${actual_url:-<empty>}"
      echo "  screenshot: ${shot_path}"
      echo
    } >>"$REPORT_PATH"
  fi
}

{
  echo "# Frontend Route Matrix Verification"
  echo "Timestamp: ${STAMP}"
  echo "Base URL: ${BASE_URL}"
  echo "Environment ID: ${ENVIRONMENT_ID}"
  echo "Run ID: ${RUN_ID}"
  echo "Playwright session: ${PW_SESSION}"
  echo
} >"$REPORT_PATH"

run_check "landing route" \
  "/" \
  "${BASE_URL}/"

run_check "canonical environments route" \
  "/environments" \
  "${BASE_URL}/environments"

run_check "canonical environment detail route" \
  "/environments/${ENVIRONMENT_ID}" \
  "${BASE_URL}/environments/${ENVIRONMENT_ID}"

run_check "canonical runs route" \
  "/runs" \
  "${BASE_URL}/runs"

run_check "canonical run short route to stats" \
  "/runs/${RUN_ID}" \
  "${BASE_URL}/runs/${RUN_ID}/stats"

run_check "canonical run stats route" \
  "/runs/${RUN_ID}/stats" \
  "${BASE_URL}/runs/${RUN_ID}/stats"

run_check "canonical run traces route" \
  "/runs/${RUN_ID}/traces" \
  "${BASE_URL}/runs/${RUN_ID}/traces"

run_check "canonical run trace detail route" \
  "/runs/${RUN_ID}/traces/${TRACE_ID}" \
  "${BASE_URL}/runs/${RUN_ID}/traces/${TRACE_ID}"

{
  echo "Summary:"
  echo "  pass: ${PASS_COUNT}"
  echo "  fail: ${FAIL_COUNT}"
} >>"$REPORT_PATH"

echo "Route matrix report: ${REPORT_PATH}"
echo "Pass: ${PASS_COUNT}  Fail: ${FAIL_COUNT}"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
