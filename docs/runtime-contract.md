# Runtime Contract

Runtime manifests live under `runtimes/<runtime-id>/runtime.yaml`.

Required fields:

- `id`
- `name`
- `agent_core`

Recommended fields:

- `description`
- `required_population_files`
- `baseline_tool_families`
- `service`
- `notes`

## Public V1 Runtime

`runtimes/openclaw/runtime.yaml` is the public runtime.

It defines:

- the baseline execution engine
- the required population files
- the baseline tool families available to populations

## Ownership Boundary

Runtime owns:

- execution model
- baseline tools
- required population file contract

Runtime does not own:

- experiment logic
- environment-specific skills
- population prompts for a specific study
