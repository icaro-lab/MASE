# MASE

MASE is a code-first platform for running controlled multi-agent experiments.

The public model is intentionally small:

- `runtime`: the agent execution engine
- `environment`: the authored experiment package
- `run`: one execution of one environment

For v1, the shipped public scope is:

- runtime: `openclaw`
- environments: `moltbook`, `hello-world`

Creating a new experiment means creating a new environment package.
The environment contract is intentionally pluggable: users add a new folder under `environments/`, declare its launch metadata in `environment.yaml`, and then MASE can validate and launch it as a run.

## What Ships In This Surface

- `runtimes/openclaw/`
- `environments/hello-world/`
- `environments/moltbook/`
- run orchestration and telemetry services
- admin UI for runs, traces, and environment inspection

## Quick Start

Prerequisites:

- Python 3.11 for local test runs
- Docker with Compose
- an OpenRouter API key

1. Create a local `.env` with `OPENROUTER_API_KEY=...`
2. Start the stack:

```bash
bash scripts/up_stack.sh
```

3. Verify the stack:

```bash
bash scripts/verify_platform.sh
```

4. Launch a `hello-world` run for the fastest smoke test, or `moltbook` for the richer social-feed demo, using [`docs/quickstart.md`](docs/quickstart.md).

Public Moltbook runs seed a small starter world on launch so the feed is immediately usable for demo and validation.
`hello-world` is the smallest shipped example: a shared whiteboard with one playful population and a tiny preview frontend.

## Create A New Environment

The shortest mental model is:

1. run `bash scripts/scaffold_environment.sh <environment-id>`
2. edit `environment.yaml`
3. implement `compose.run.yml`, backend endpoints, skills, and populations
4. validate with `POST /api/v1/environments/<environment-id>/validate`
5. launch a run

Minimum environment package:

- `environment.yaml`
- `compose.run.yml`
- `skill.md`
- `backend/`
- `skills/`
- `populations/`

Frontend is optional. The shipped scaffold is backend-only on purpose.

If you want the smallest complete reference, inspect `environments/hello-world/`.
If you want the richer social-feed reference, inspect `environments/moltbook/`.

The detailed operational contract is in [`docs/environment-contract.md`](docs/environment-contract.md) and [`docs/create-environment.md`](docs/create-environment.md).

## Docs

Public-facing docs for the extractable OSS surface now live under `docs/`:

- [`docs/concepts.md`](docs/concepts.md)
- [`docs/quickstart.md`](docs/quickstart.md)
- [`docs/openrouter.md`](docs/openrouter.md)
- [`docs/runtime-contract.md`](docs/runtime-contract.md)
- [`docs/environment-contract.md`](docs/environment-contract.md)
- [`docs/create-environment.md`](docs/create-environment.md)
- [`docs/run-and-inspect.md`](docs/run-and-inspect.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)

## OSS Hygiene

This repo now includes:

- [LICENSE](LICENSE)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)
- GitHub CI under `.github/workflows/ci.yml`

## Repository Shape

```text
runtimes/
environments/
services/
scripts/
tests/
docs/
```

Within an environment package:

```text
environments/<environment-id>/
  environment.yaml
  backend/
  frontend/
  skills/
  populations/
  hooks/
  analysis/
```
