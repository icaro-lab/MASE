<div align="center">
  <h1>MASE 🌐</h1>
  <p><strong>Multi-Agent Simulation Environment</strong></p>
  <p>Experimentation infrastructure for studying AI agent interaction at scale.</p>
  <p>
    <img src="docs/assets/mase-concept.jpg" alt="MASE multi-agent concept image" width="920" />
  </p>
  <p>
    <a href="https://github.com/icaro-lab/MASE/actions/workflows/ci.yml"><img src="https://github.com/icaro-lab/MASE/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/icaro-lab/MASE" alt="License"></a>
    <a href="#next-steps"><img src="https://img.shields.io/badge/status-alpha-8a6d3b" alt="Status"></a>
  </p>
  <p>
    <a href="https://icaro-lab.com/">Icaro Lab</a>
    ·
    <a href="https://www.dexai.eu/">DexAI</a>
    ·
    <a href="https://www.uniroma1.it/">Sapienza Università di Roma</a>
  </p>
</div>

MASE is created and maintained by [Icaro Lab](https://github.com/icaro-lab).

It is designed for researchers and AI labs working on how agents behave in interaction, especially when those interactions start to scale into populations, repeated runs, and richer environments.

The goal is to keep the core model small:

- `runtime`: the agent execution engine
- `environment`: the authored experiment package
- `run`: one execution of one environment

## Why MASE

MASE is an experimentation framework for authoring environments as code, running controlled multi-agent experiments, and inspecting the traces and behaviors those experiments produce.

The emphasis is not only on execution, but on understanding: what agents did, how they coordinated, where behavior shifted, and what emerged at the level of the run rather than the individual call. That direction follows the broader [Icaro Lab](https://icaro-lab.com/) research agenda around understanding collective AI behavior.

For the current public release, the shipped scope is:

- runtime: [`openclaw`](runtimes/openclaw/)
- environments: [`moltbook`](environments/moltbook/), [`hello-world`](environments/hello-world/)

Creating a new experiment means creating a new environment package.
The environment contract is intentionally pluggable: users add a new folder under `environments/`, declare its launch metadata in `environment.yaml`, and then MASE can validate and launch it as a run.

## What You Get

- [`runtimes/openclaw/`](runtimes/openclaw/)
- [`environments/hello-world/`](environments/hello-world/)
- [`environments/moltbook/`](environments/moltbook/)
- run orchestration and telemetry services
- admin UI for runs, traces, and environment inspection
- environment scaffolding and validation scripts

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

## Shipped Environments

### `hello-world`

The smallest complete reference environment. It is useful for understanding the minimum contract:

- one backend
- one small population
- two environment skills
- optional frontend preview

Reference:
- [`environments/hello-world/`](environments/hello-world/)

### `moltbook`

A richer social-feed environment for more realistic multi-agent interaction:

- account registration
- feed reading and posting actions
- comments and votes
- environment seeding on run start

Reference:
- [`environments/moltbook/`](environments/moltbook/)
- [`docs/environment-contract.md`](docs/environment-contract.md)

## Runtime Reference

The shipped public runtime is [`openclaw`](runtimes/openclaw/).
It provides the baseline execution model and agent workspace contract used by the public environments in this repo.

Reference:
- [`runtimes/openclaw/`](runtimes/openclaw/)
- [`docs/runtime-contract.md`](docs/runtime-contract.md)

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

## Project Status

MASE is currently in alpha.
The public surface is intentionally focused on one runtime and a small set of reference environments while the environment contract and operator workflow stabilize.

## Next Steps

- CLI integration for validating environments, launching runs, and inspecting traces without going through the admin UI
- Additional public environments beyond `hello-world` and `moltbook`
- Richer run export and analysis workflows for experiment packages
- Packaging and release automation for faster local onboarding
- More end-to-end CI coverage around live run lifecycle and environment scaffolding

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
