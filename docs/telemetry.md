# Telemetry

MASE persists run-scoped telemetry at two levels:

- `events`: general run/system events
- `agent_action_events`: per-action rows emitted by the runtime

The most useful starting point is `action_attempt` telemetry. Those rows already include:

- action identity (`action_type`, `action_name`, `action_key`)
- request metadata (`method`, `path`, `status_code`, `request_id`)
- timing and success/failure fields
- a bounded `response_preview`

For JSON-like HTTP responses, MASE now also stores:

- `response_snapshot`
- `response_snapshot_meta`

The snapshot is intentionally bounded rather than a raw body dump:

- nested depth is capped
- dict keys and list items are capped
- long strings are truncated
- sensitive-looking values are redacted

This makes telemetry usable for downstream analysis without turning the controller database into a
full packet capture.

## Querying

Useful endpoints:

- `GET /api/v1/telemetry/events/{run_id}`
- `GET /api/v1/telemetry/metrics/{run_id}`
- `GET /api/v1/runs/{run_id}/events`

## Tuning

Agent-launcher snapshot limits are configurable via environment variables:

- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_ENABLED`
- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_MAX_DEPTH`
- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_MAX_DICT_KEYS`
- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_MAX_LIST_ITEMS`
- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_MAX_STRING_CHARS`
- `AGENT_LAUNCHER_HTTP_RESPONSE_SNAPSHOT_MAX_TOTAL_NODES`

## Extension Pattern

The generic snapshot layer should remain environment-agnostic.

If an environment needs domain-specific analysis, the recommended pattern is:

1. capture a bounded generic `response_snapshot` in MASE
2. derive environment-specific projections in the environment repo or downstream analysis pipeline

That keeps core telemetry reusable while still supporting richer experiment-specific metrics.
