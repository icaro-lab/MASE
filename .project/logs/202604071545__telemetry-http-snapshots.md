# Telemetry HTTP Snapshots

## Scope

- add bounded structured HTTP response snapshots to action telemetry
- keep the feature generic in public MASE
- document the persisted telemetry surfaces and snapshot limits

## Changed Surfaces

- `services/agent-launcher/app/http_snapshot.py`
- `services/agent-launcher/app/config.py`
- `services/agent-launcher/app/executor.py`
- `services/agent-launcher/tests/unit/test_http_snapshot.py`
- `services/agent-launcher/tests/integration/test_heartbeat_endpoint_multiround.py`
- `docs/telemetry.md`
- `docs/run-and-inspect.md`
- `README.md`

## Notes

- snapshots are bounded and redacted, not raw body dumps
- controller storage/export path already carries payload JSON, so no controller schema change was needed
- this is intended to support later environment-specific projections such as feed score/rank analysis
