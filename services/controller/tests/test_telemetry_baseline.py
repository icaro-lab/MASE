"""Unit tests for run-scoped telemetry baseline enrichment."""

from __future__ import annotations

import types

import pytest

from app.telemetry_baseline import build_experiment_baseline_fields


class _NoQueryResult:
    def __init__(self, value=None):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value


class _NoQuerySession:
    def __init__(self, agent_assignment=None):
        self._agent_assignment = agent_assignment

    def query(self, model, *_args, **_kwargs):
        if getattr(model, "__name__", "") == "RunAgentAssignment":
            return _NoQueryResult(self._agent_assignment)
        return _NoQueryResult(None)


@pytest.mark.unit
def test_build_experiment_baseline_fields_from_assignment_cache() -> None:
    run = types.SimpleNamespace(
        run_id="run-1",
        experiment_policy_hash="sha256:policy",
        experiment_manifest_hash="sha256:manifest",
        experiment_assignment_hash="sha256:assignment",
        experiment_policy_json={"policy_version": "1.0"},
    )

    fields = build_experiment_baseline_fields(
        _NoQuerySession(),
        run=run,
        agent_id="agent-3",
        assignment_cache={
            "agent-3": {
                "runtime_id": "openclaw-disinfo",
                "population_group": "treated",
                "role": "disinfo_actor",
                "model_id": "openai/gpt-5-mini",
            }
        },
    )

    assert fields["policy_hash"] == "sha256:policy"
    assert fields["manifest_hash"] == "sha256:manifest"
    assert fields["assignment_hash"] == "sha256:assignment"
    assert fields["policy_version"] == "1.0"
    assert fields["population_group"] == "treated"
    assert fields["role"] == "disinfo_actor"
    assert fields["runtime_id"] == "openclaw-disinfo"
    assert fields["model_id"] == "openai/gpt-5-mini"


@pytest.mark.unit
def test_build_experiment_baseline_fields_defaults_when_no_agent_override(monkeypatch) -> None:
    run = types.SimpleNamespace(
        run_id="run-2",
        experiment_policy_hash=None,
        experiment_manifest_hash=None,
        experiment_assignment_hash=None,
        experiment_policy_json={},
    )

    monkeypatch.setattr(
        "app.telemetry_baseline.run_binding.build_run_context",
        lambda _run, _db: {
            "environment_config": {
                "runtime_id": "openclaw",
                "agent_model": "openai/gpt-5-mini",
            }
        },
    )

    fields = build_experiment_baseline_fields(
        _NoQuerySession(),
        run=run,
        agent_id=None,
    )

    assert fields["policy_hash"] is None
    assert fields["manifest_hash"] is None
    assert fields["assignment_hash"] is None
    assert fields["policy_version"] is None
    assert fields["population_group"] is None
    assert fields["role"] is None
    assert fields["runtime_id"] == "openclaw"
    assert fields["model_id"] == "openai/gpt-5-mini"


@pytest.mark.unit
def test_build_experiment_baseline_fields_reads_agent_assignment_row_when_cache_missing(monkeypatch) -> None:
    run = types.SimpleNamespace(
        run_id="run-2b",
        experiment_policy_hash="sha256:policy",
        experiment_manifest_hash="sha256:manifest",
        experiment_assignment_hash="sha256:assignment",
        experiment_policy_json={"policy_version": "1.0"},
    )

    monkeypatch.setattr(
        "app.telemetry_baseline.run_binding.build_run_context",
        lambda _run, _db: {
            "environment_config": {
                "runtime_id": "openclaw",
                "agent_model": "openai/gpt-5-mini",
            }
        },
    )

    fields = build_experiment_baseline_fields(
        _NoQuerySession(
            agent_assignment=types.SimpleNamespace(
                runtime_id="openclaw-treated",
                population_group="treated",
                role_label="disinfo_actor",
                model_id="openai/gpt-5",
            )
        ),
        run=run,
        agent_id="agent-2",
        assignment_cache={},
    )

    assert fields["runtime_id"] == "openclaw-treated"
    assert fields["population_group"] == "treated"
    assert fields["role"] == "disinfo_actor"
    assert fields["model_id"] == "openai/gpt-5"
