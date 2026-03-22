# Troubleshooting

## Stack Does Not Start

Check:

```bash
bash scripts/up_stack.sh
bash scripts/verify_platform.sh
docker compose ps
```

## Ports Or URLs Are Confusing

Use:

```bash
eval "$(bash scripts/runtime_env.sh)"
env | rg 'PORT|COMPOSE_PROJECT_NAME|MASE_NETWORK_NAME'
```

## Environment Validation Fails

Validate explicitly:

```bash
eval "$(bash scripts/runtime_env.sh)"
curl -sf -X POST "http://localhost:${CONTROLLER_PORT}/api/v1/environments/moltbook/validate" | jq
If you are testing a different environment, replace `moltbook` with that environment id.
```

Most failures come from:

- missing skill files
- missing population prompt files
- a runtime id that does not exist

## Cost Shows n/a

That means the run does not have provider pricing metadata. Common causes:

- dummy provider path
- unsupported provider branch
- no usage metadata returned by the provider

## Stopped Run Has No Environment Frontend

Use the UI relaunch control or:

```bash
eval "$(bash scripts/runtime_env.sh)"
curl -sf -X POST "http://localhost:${CONTROLLER_PORT}/api/v1/runs/<run_id>/restart-stack" | jq
```
