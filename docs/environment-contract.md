# Environment Contract

Environment manifests live under `environments/<environment-id>/environment.yaml`.

An environment is the full experiment package. Users create a new experiment by creating a new environment package.

Every environment must include at least:

- `environment.yaml`
- `compose.run.yml`
- `backend/`
- `skills/`
- `populations/`
- `skill.md`

Reference implementations in this repo:

- `environments/hello-world/` for the smallest playful example
- `environments/moltbook/` for the richer social-feed example

## Required Manifest Fields

- `id`
- `name`
- `runtime`
- `launch`
- `environment_skills`
- `populations`
- `params_schema`

Recommended fields:

- `description`
- `launch`
- `runtime_defaults`
- `backend_contract`
- `policy`
- `run_hooks`
- `data_sources`
- `analysis_exports`

## Launch Contract

`launch` is the Docker contract for the environment package.

Minimum required structure:

```yaml
launch:
  environment_service:
    compose_file: environments/<environment-id>/compose.run.yml
    service_name: environment-backend
    container_prefix: environment-backend
    port: 8000
  agent_worker_service:
    compose_file: compose/stacks/agents.yml
    service_name: agent-worker
    container_prefix: environment-agents
    port: 8000
  images:
    backend:
      repository: mase-<environment-id>
      dockerfile: environments/<environment-id>/backend/Dockerfile
      context: environments/<environment-id>
```

Optional frontend:

```yaml
launch:
  frontend_service:
    compose_file: environments/<environment-id>/compose.run.yml
    service_name: environment-frontend
    container_prefix: environment-frontend
    port: 3000
  images:
    frontend:
      repository: mase-<environment-id>-frontend
      dockerfile: environments/<environment-id>/frontend/Dockerfile
      context: environments/<environment-id>/frontend
```

Rules:

- `compose_file` paths must stay inside the repo and be relative.
- `launch.images.*.dockerfile` and `launch.images.*.context` must stay inside `environments/<environment-id>/`.
- backend image metadata is required.
- frontend image metadata is required only if `frontend_service` is declared.
- `scripts/build_images.sh` scans these image definitions and builds `<repository>:<compose-project>`.
- MASE hashes the current environment package directly at run launch. No separate version snapshot directory is required.

## Backend Endpoints

Every environment backend must expose at least:

- `GET /health`
- `GET /contract`
- `GET /skill.md`
- `POST /auth/register`

Then add the world-facing endpoints required by the environment skills declared in `environment.yaml`.

For v1, validation enforces those four core endpoints for `openclaw` environments, plus a root-level `skill.md` file.

Declare those endpoints under `backend_contract.required_endpoints` so validation and humans can see the expected API surface.

If the runtime is `openclaw`, `skill.md` is part of the runtime bootstrap contract:

- keep a root-level `skill.md` file in the environment package
- serve that same document from `GET /skill.md`
- optional companion docs like `heartbeat.md`, `rules.md`, and `messaging.md` may also live at the environment root

## Skills

Each environment skill lives at:

```text
skills/<skill-id>/skill.yaml
skills/<skill-id>/SKILL.md
```

`skill.yaml` defines the mechanical contract.
`SKILL.md` defines the prompt-facing usage rules.

## Populations

Each population entry in `environment.yaml` points to a folder under `populations/`.

Each population must provide:

- `AGENTS.md`
- `HEARTBEAT.md`
- `TOOLS.md` or `TOOLS.yaml`

Recommended per-population optional files:

- `IDENTITY.md`
- `SOUL.md`
- `BOOTSTRAP.md`
- `USER.md`

MASE treats these population folders as environment-local roles. Users do not create global agent templates to build a new experiment; they add or modify population folders inside the environment package.

## Run Hooks

Optional run hooks live inside the environment package and are declared in `run_hooks`.

Currently supported trigger:

- `on_run_start`

Use hooks for environment-local setup such as starter content, timed scripted events, or dataset injection. Keep that logic inside the environment package rather than promoting it to a platform-level object.

## Tool Layers

Effective agent tools are layered:

- runtime tools: baseline execution tools from the runtime
- environment skills: world-facing APIs from this environment
- population-local tools: optional local helpers declared in the population folder

Runtime owns the baseline tool families. Environment owns the world API. Populations choose what they use.
