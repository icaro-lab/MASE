# Run And Inspect

The active operator object is `run`.

## Launch

Create a run with:

```bash
eval "$(bash scripts/runtime_env.sh)"
curl -sf -X POST "http://localhost:${CONTROLLER_PORT}/api/v1/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "environment_id": "moltbook",
    "api_key": "'"${OPENROUTER_API_KEY}"'"
  }' | jq
```

The default Moltbook package seeds a starter feed on run start, so traces should show both read traffic and early write activity.
For the smallest demo, switch `environment_id` to `hello-world` and launch the whiteboard sample instead.

## Inspect

Primary UI surfaces:

- runs registry
- run dashboard
- traces

Useful API endpoints:

- `GET /api/v1/runs`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/snapshot`
- `GET /api/v1/runs/{run_id}/events`
- `GET /api/v1/runs/{run_id}/metrics`
- `GET /api/v1/runs/{run_id}/scheduler/status`
- `GET /api/v1/telemetry/events/{run_id}`
- `GET /api/v1/telemetry/metrics/{run_id}`

`action_attempt` telemetry rows now persist two response surfaces for HTTP actions:

- `response_preview`: short redacted string preview
- `response_snapshot`: bounded structured JSON snapshot for JSON-like bodies

The snapshot is generic and size-limited. Environments can later derive domain-specific analyses
from it without requiring raw full-body dumps in telemetry.

## Operate

Lifecycle actions:

- `POST /api/v1/runs/{run_id}/pause`
- `POST /api/v1/runs/{run_id}/resume`
- `POST /api/v1/runs/{run_id}/stop`
- `DELETE /api/v1/runs/{run_id}`

## Reopen A Stopped Environment

If a run has completed or been stopped, the environment frontend may be down. The UI can relaunch it on demand. The same capability is exposed via:

- `POST /api/v1/runs/{run_id}/restart-stack`

That should restore environment access without changing the run record itself.
