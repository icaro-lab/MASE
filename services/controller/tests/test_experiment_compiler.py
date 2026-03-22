"""Unit tests for experiment policy compiler helpers."""

import pytest

from app.experiment_compiler import (
    ExperimentCompilerError,
    compile_policy_with_group_overrides,
    compute_compiled_experiment_hash,
    validate_policy_compile_inputs,
)

@pytest.mark.unit
def test_validate_policy_compile_inputs_rejects_inline_env_override() -> None:
    with pytest.raises(ExperimentCompilerError) as exc:
        validate_policy_compile_inputs(
            {
                "experiment_policy": {
                    "policy_version": "1.0",
                    "core": {"group_overrides": {"treated": {"share": 0.5}}},
                    "env": {"moltbook": {"compass": {"interval_minutes": 10}}},
                }
            }
        )
    assert exc.value.code == "unsupported_feature"
    assert "does not allow inline environment policy overrides" in str(exc.value)


@pytest.mark.unit
def test_compile_policy_with_group_overrides_applies_group_layer() -> None:
    compiled = compile_policy_with_group_overrides(
        base_policy_json={
            "policy_version": "1.0",
            "core": {
                "required_capabilities": ["population_mix"],
                "population_groups": {
                    "treated": {
                        "share": 1.0,
                        "runtime_id": "openclaw",
                        "model_id": "openai/gpt-5-mini",
                    }
                },
            },
            "env": {},
        },
        group_overrides={
            "treated": {
                "model_id": "google/gemini-2.5-flash",
            }
        },
    )

    assert compiled["core"]["required_capabilities"] == ["population_mix"]
    assert compiled["core"]["group_overrides"]["treated"]["model_id"] == "google/gemini-2.5-flash"


@pytest.mark.unit
def test_compile_policy_with_group_overrides_deep_merges_base_policy_and_runtime() -> None:
    compiled = compile_policy_with_group_overrides(
        base_policy_json={
            "policy_version": "1.0",
            "core": {
                "required_capabilities": ["population_mix"],
                "population_groups": {
                    "control": {
                        "share": 0.3,
                        "runtime_id": "openclaw",
                        "model_id": "openai/gpt-5-mini",
                    },
                    "treated": {
                        "share": 0.4,
                        "runtime_id": "openclaw",
                        "model_id": "openai/gpt-5-mini",
                    },
                    "new_group": {
                        "share": 0.3,
                        "runtime_id": "openclaw",
                        "model_id": "openai/gpt-5-mini",
                    },
                },
                "group_overrides": {
                    "control": {"role_label": "baseline"},
                    "treated": {
                        "runtime_id": "openclaw",
                    },
                }
            },
            "env": {},
        },
        group_overrides={
            "treated": {"model_id": "google/gemini-2.5-flash"},
            "new_group": {"role_label": "fresh"},
        },
    )

    overrides = compiled["core"]["group_overrides"]
    assert overrides["control"]["role_label"] == "baseline"
    assert overrides["treated"]["runtime_id"] == "openclaw"
    assert overrides["treated"]["model_id"] == "google/gemini-2.5-flash"
    assert overrides["new_group"]["role_label"] == "fresh"


@pytest.mark.unit
def test_compile_policy_with_group_overrides_rejects_agent_overrides() -> None:
    with pytest.raises(ExperimentCompilerError) as exc:
        compile_policy_with_group_overrides(
            base_policy_json={
                "policy_version": "1.0",
                "core": {"agent_overrides": {"agent-1": {"role": "disinfo_actor"}}},
                "env": {},
            },
            group_overrides={},
        )
    assert exc.value.code == "unsupported_feature"
    assert "Per-agent overrides are deferred" in str(exc.value)


@pytest.mark.unit
def test_compute_compiled_experiment_hash_changes_with_inputs() -> None:
    first = compute_compiled_experiment_hash(
        environment_ref="environment/moltbook@1.1.0",
        policy_hash="sha256:policy-a",
        manifest_hash="sha256:manifest-a",
        compile_source="profile",
    )
    second = compute_compiled_experiment_hash(
        environment_ref="environment/moltbook@1.1.0",
        policy_hash="sha256:policy-a",
        manifest_hash="sha256:manifest-a",
        compile_source="profile",
    )
    changed = compute_compiled_experiment_hash(
        environment_ref="environment/moltbook@1.1.0",
        policy_hash="sha256:policy-a",
        manifest_hash="sha256:manifest-a",
        compile_source="inline_legacy",
    )

    assert first == second
    assert first != changed
