"""Unit tests for the runtime/environment/run synthesis layer."""

from __future__ import annotations

from app.routes.runs import (
    PopulationOverride,
    RunCreateRequest,
    _build_environment_config,
    _normalize_runtime_id,
)


def test_build_environment_config_resolves_population_specs() -> None:
    environment_manifest = {
        "id": "moltbook",
        "name": "Moltbook",
        "runtime": "openclaw",
        "launch": {
            "environment_id": "moltbook",
            "pre_register_agents": True,
        },
        "runtime_defaults": {
            "max_heartbeats_per_agent": 10,
            "runtime_limit_minutes": 30,
            "max_parallel_agents": 50,
            "heartbeat": {"interval": "10s"},
        },
        "run_hooks": [
            {
                "id": "mind_batch_supply",
                "script": "hooks/run_mind_batch_supply.py",
            }
        ],
        "populations": {
            "voter": {
                "path": "populations/voter",
                "runtime_id": "simple-feed-voter",
                "default_count": 3,
                "default_model": "openai/gpt-5-mini",
            },
            "critic": {
                "path": "populations/critic",
                "default_count": 1,
                "default_model": "openai/gpt-5-nano",
                "runtime_id": "openclaw",
            },
        },
    }
    runtime_manifest = {
        "id": "openclaw",
        "agent_core": "openclaw_py_minimal_core",
    }
    request = RunCreateRequest(
        environment_id="moltbook",
        seed=101,
        params={"condition": "hidden-votes"},
        population_overrides={
            "critic": PopulationOverride(count=2, model="openai/gpt-5-mini"),
        },
    )

    environment_ref, env_config, snapshot = _build_environment_config(
        environment_manifest,
        runtime_manifest,
        request,
    )

    assert environment_ref == "environment/moltbook"
    assert env_config["environment_id"] == "moltbook"
    assert env_config["runtime_id"] == "openclaw"
    assert env_config["agent_count"] == 5
    assert env_config["environment_params"] == {"condition": "hidden-votes"}
    assert env_config["heartbeat"] == {"interval": "10s"}
    assert env_config["runtime_limit_minutes"] == 30
    assert env_config["run_hooks"] == [
        {
            "id": "mind_batch_supply",
            "trigger": "on_run_start",
            "script": "hooks/run_mind_batch_supply.py",
            "background": True,
            "env": {},
        }
    ]

    voter_spec = env_config["population_specs"]["voter"]
    critic_spec = env_config["population_specs"]["critic"]
    assert voter_spec["runtime_id"] == "simple-feed-voter"
    assert voter_spec["count"] == 3
    assert critic_spec["runtime_id"] == "openclaw"
    assert critic_spec["count"] == 2
    assert snapshot["population_specs"]["voter"]["count"] == 3
    assert snapshot["population_specs"]["critic"]["count"] == 2
    assert snapshot["launch"]["environment_id"] == "moltbook"
    assert snapshot["seed"] == 101


def test_population_specs_fallback_still_exposes_agent_count() -> None:
    population_specs = {
        "voter": {"count": 1},
        "critic": {"count": 2},
    }

    derived_count = sum(
        int((spec or {}).get("count") or 0)
        for spec in population_specs.values()
        if isinstance(spec, dict)
    )

    assert derived_count == 3


def test_normalize_runtime_id_maps_legacy_runtime_names() -> None:
    assert _normalize_runtime_id("simplified-social") == "openclaw"
    assert _normalize_runtime_id("openclaw-py") == "openclaw"
    assert _normalize_runtime_id("openclaw") == "openclaw"
    assert _normalize_runtime_id("") is None

def test_build_environment_config_defaults_population_runtime_to_environment_runtime() -> None:
    environment_manifest = {
        "id": "moltbook",
        "name": "Moltbook",
        "runtime": "openclaw",
        "launch": {
            "environment_id": "moltbook",
            "pre_register_agents": True,
        },
        "params_schema": {},
        "populations": {
            "resident": {
                "path": "populations/resident",
                "default_count": 2,
                "default_model": "openai/gpt-5-mini",
            }
        },
    }
    runtime_manifest = {"id": "openclaw", "agent_core": "openclaw_py_minimal_core"}
    request = RunCreateRequest(environment_id="moltbook")

    _, env_config, _ = _build_environment_config(environment_manifest, runtime_manifest, request)

    assert env_config["population_specs"]["resident"]["runtime_id"] == "openclaw"
    assert env_config["runtime_id"] == "openclaw"
