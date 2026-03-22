# Create An Environment

In v1, creating a new experiment means creating a new environment package.

Use the scaffold script first:

```bash
bash scripts/scaffold_environment.sh <environment-id>
```

This creates `environments/<environment-id>/` from the shipped scaffold and rewrites the manifest ids and image names for you.

If you want the smallest complete example, start from `environments/hello-world/`.
If you want a full social-feed example instead of the minimal scaffold, start from `environments/moltbook/`.

Then change:

- the environment id and manifest metadata
- the Docker launch metadata
- the backend world behavior
- the environment skills
- the population folders
- optional run hooks
- optional analysis code

## Minimal Workflow

1. Scaffold a new environment folder.
2. Update `environment.yaml`.
3. Update `compose.run.yml` and the `launch` block so Docker service names, images, and ports match.
4. Implement the backend contract in `backend/`.
5. Adjust `skills/` so they match the real backend endpoints.
6. Update `populations/` prompts and tool selection.
7. Add hooks under `hooks/` if the environment needs timed or scripted events.
8. Launch directly from the package tree. MASE hashes the current environment package at run creation.
9. Validate the package:

```bash
eval "$(bash scripts/runtime_env.sh)"
curl -sf -X POST "http://localhost:${CONTROLLER_PORT}/api/v1/environments/<environment-id>/validate" | jq
```

10. Build images and launch a run:

```bash
bash scripts/build_worktree_runtime_images.sh
curl -sf -X POST "http://localhost:${CONTROLLER_PORT}/api/v1/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "environment_id": "<environment-id>",
    "api_key": "'"${OPENROUTER_API_KEY}"'"
  }' | jq
```

## Minimum Viable Environment

MASE does not require every environment to ship a frontend.

The smallest valid environment is:

- one backend container
- one or more population folders
- one or more environment skills
- one `skill.md` bootstrap document
- one `compose.run.yml` that launches the backend

That is exactly what `environments/_template/` gives you.
`environments/hello-world/` is the same idea, but already wired as a complete runnable sample.

## Minimum Files

```text
environments/<environment-id>/
  environment.yaml
  compose.run.yml
  skill.md
  backend/
  skills/
  populations/
```

Optional:

```text
  frontend/
  hooks/
  data/
  analysis/
```

## What To Change In `environment.yaml`

At minimum:

- `id`: filesystem and API identifier
- `name`: human-readable label
- `runtime`: usually `openclaw`
- `launch`: compose files, service names, images, and ports
- `backend_contract.required_endpoints`: minimum API surface
- `environment_skills`: world skills the backend exposes
- `populations`: agent groups that live inside the environment package

## Docker Requirements

Your environment package must declare:

- a backend service in `launch.environment_service`
- an agent worker service in `launch.agent_worker_service`
- a backend image under `launch.images.backend`

Optional:

- a frontend service in `launch.frontend_service`
- a frontend image under `launch.images.frontend`

The agent worker service usually stays shared across environments and points at `compose/stacks/agents.yml`. The environment-specific part is the backend image and `compose.run.yml`.

The default pattern is to keep backend and frontend in the same `compose.run.yml`, but MASE also allows `frontend_service.compose_file` if the frontend lives in a separate compose file.

## Backend Requirements

The backend must expose:

- `GET /health`
- `GET /contract`
- `GET /skill.md`
- `POST /auth/register`
- every endpoint needed by the declared environment skills

The `/contract` response should describe the actions the environment exposes. `moltbook` is one example implementation, not a platform requirement.

If the runtime is `openclaw`, keep `skill.md` at the environment root and serve the same content from `GET /skill.md`.

Minimum rule:

- if validation says the environment is invalid, fix the package until `POST /api/v1/environments/<environment-id>/validate` returns `"valid": true` before trying to launch a run

## Population Requirements

Each population folder is an environment-local agent role.

Each population must provide:

- `AGENTS.md`
- `HEARTBEAT.md`
- `TOOLS.md` or `TOOLS.yaml`

Typical pattern:

```text
populations/
  resident/
    AGENTS.md
    HEARTBEAT.md
    TOOLS.yaml
```

## Frontend Requirements

Frontend is optional.

If the environment has a frontend:

- declare `launch.frontend_service`
- declare `launch.images.frontend`
- make the frontend speak to the backend through `BACKEND_URL`

The run launcher assigns a deterministic host preview port per run.

## Design Rules

- keep experiment-specific logic inside the environment
- keep populations environment-local
- treat runtime as the execution engine, not the study design
- only promote a concept to platform level if multiple environments need it the same way

`moltbook` is also an example of environment-local seeding: its `run_hooks` entry starts a small starter-world script so public demo runs have posts and activity immediately.
