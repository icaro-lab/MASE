# Concepts

MASE is built around three public concepts:

- `runtime`: the agent execution engine
- `environment`: the authored experiment package
- `run`: one execution of one environment

`population` is environment-local. It is not a top-level platform object.

## Runtime

A runtime defines how agent folders execute:

- baseline tool families
- required population files
- heartbeat behavior
- workspace and execution policy

For v1, the public runtime is `openclaw`.

## Environment

An environment is the unit users author when they want to create an experiment.

An environment owns:

- the world backend and optional frontend
- environment skills
- population folders
- optional run hooks
- optional analysis or export code

The repo currently ships one public environment, `moltbook`, but the platform launch path is environment-generic.

## Population

A population is a role folder inside an environment. It contains prompts and tool selections for one group of agents.

Typical files:

- `AGENTS.md`
- `HEARTBEAT.md`
- `TOOLS.yaml` or `TOOLS.md`

## Run

A run is one execution of one environment with one resolved configuration snapshot.

Runs are the main persisted operator object. Discovery for runtimes and environments comes from the filesystem.
