# Contributing To MASE

## Scope

This repository is centered on three concepts:

- `runtime`
- `environment`
- `run`

New experiments should usually be contributed as new environment packages under `environments/`.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Set `OPENROUTER_API_KEY`.
3. Start the stack:

```bash
bash scripts/up_stack.sh
```

4. Verify the stack:

```bash
bash scripts/verify_platform.sh
```

## Branch Strategy

- `main` is the stable release branch.
- `dev` is the integration branch for active development.
- Open pull requests against `dev`, not `main`.
- Do not push feature work directly to `main`.

Recommended flow:

```bash
git fetch origin
git switch dev
git pull --rebase origin dev
git switch -c feat/<short-topic>
```

If you are contributing from a fork, open your PR from `feat/<short-topic>` into `icaro-lab/MASE:dev`.

## Main Contribution Paths

### New Environment

Use the scaffold:

```bash
bash scripts/scaffold_environment.sh <environment-id>
```

Then follow:

- [docs/create-environment.md](docs/create-environment.md)
- [docs/environment-contract.md](docs/environment-contract.md)

### Runtime Work

If you are changing `runtimes/openclaw/` or the launcher/controller behavior, keep changes aligned with:

- [docs/runtime-contract.md](docs/runtime-contract.md)
- [docs/concepts.md](docs/concepts.md)

## Checks

Run the relevant checks for your slice before opening a PR.

Core controller checks:

```bash
cd services/controller
PYTHONPATH=. pytest tests/test_catalog.py tests/test_runs_domain_reset.py tests/test_public_runs_routes.py -q
```

Admin frontend:

```bash
npm --prefix services/admin/frontend test -- --run
npm --prefix services/admin/frontend run build
```

Whole-stack smoke:

```bash
bash scripts/verify_platform.sh
```

## Pull Requests

- Keep PRs scoped.
- Base PRs on `dev`.
- Describe the user-facing change clearly.
- Include the commands you ran.
- Call out any deferred work explicitly.
- Rebase or merge from `origin/dev` before marking the PR ready.

## Design Rules

- Keep the platform environment-generic.
- Do not introduce new top-level experiment abstraction layers beyond runtime, environment, and run.
- If a concept is specific to one study, keep it inside that environment package.
