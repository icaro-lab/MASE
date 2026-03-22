# OpenRouter

MASE expects one canonical provider variable:

```bash
OPENROUTER_API_KEY=your-key
```

The current branch forwards this value to both the controller and the agent launcher. You do not need to set separate `SIM_CTRL_*` or `AGENT_LAUNCHER_*` provider keys for normal local usage.

If you use the shell helpers, `eval "$(bash scripts/runtime_env.sh)"` also re-exports `OPENROUTER_API_KEY` from the local repo `.env` when present.

## Verify The Wiring

```bash
eval "$(bash scripts/runtime_env.sh)"
docker compose exec controller env | rg OPENROUTER
docker compose exec agent-launcher env | rg OPENROUTER
```

## Verify The Key

Check the actual key before debugging agent behavior:

```bash
curl -sS https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5-mini",
    "messages": [{"role": "user", "content": "Reply with OK"}],
    "max_tokens": 10
  }' | jq
```

If OpenRouter returns `401` with `User not found.`, the key itself is invalid or revoked. Fix the key first; MASE cannot recover from that inside the runtime.

## Pricing

Real prices appear only when the run is using a real priced provider path.

If the UI shows `n/a` for cost, check:

- the run was not launched with `api_key=dummy`
- the selected models are available through OpenRouter
- the trace row contains usage metadata from the provider
