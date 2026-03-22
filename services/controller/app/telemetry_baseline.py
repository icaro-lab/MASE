"""Shared telemetry baseline enrichment helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.database import Run as RunDB
from app.database import RunAgentAssignment as RunAgentAssignmentDB
from app import run_binding


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_optional_string(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _resolve_agent_assignment_metadata(
    db: Session,
    *,
    run: RunDB,
    agent_id: Optional[str],
    assignment_cache: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
) -> Dict[str, Optional[str]]:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return {}

    if assignment_cache is not None and normalized_agent_id in assignment_cache:
        return dict(assignment_cache.get(normalized_agent_id) or {})

    assignment = (
        db.query(RunAgentAssignmentDB)
        .filter(RunAgentAssignmentDB.run_id == run.run_id)
        .filter(RunAgentAssignmentDB.runtime_agent_id == normalized_agent_id)
        .first()
    )
    metadata = {
        "runtime_id": _normalize_optional_string(getattr(assignment, "runtime_id", None)),
        "population_group": _normalize_optional_string(getattr(assignment, "population_group", None)),
        "role": _normalize_optional_string(getattr(assignment, "role_label", None)),
        "model_id": _normalize_optional_string(getattr(assignment, "model_id", None)),
    }
    if assignment_cache is not None:
        assignment_cache[normalized_agent_id] = dict(metadata)
    return metadata


def build_experiment_baseline_fields(
    db: Session,
    *,
    run: RunDB,
    agent_id: Optional[str] = None,
    assignment_cache: Optional[Dict[str, Dict[str, Optional[str]]]] = None,
) -> Dict[str, Any]:
    """Build stable experiment join fields for telemetry payloads."""

    context = run_binding.build_run_context(run, db)
    env_config = _as_dict(context.get("environment_config"))
    policy_json = _as_dict(getattr(run, "experiment_policy_json", None))
    default_model_id = str(env_config.get("agent_model") or "").strip() or None

    assignment_metadata = _resolve_agent_assignment_metadata(
        db,
        run=run,
        agent_id=agent_id,
        assignment_cache=assignment_cache,
    )
    runtime_id = assignment_metadata.get("runtime_id")
    if not runtime_id:
        runtime_id = str(env_config.get("runtime_id") or "").strip() or None

    return {
        "policy_hash": str(getattr(run, "experiment_policy_hash", "") or "").strip() or None,
        "manifest_hash": str(getattr(run, "experiment_manifest_hash", "") or "").strip() or None,
        "assignment_hash": str(getattr(run, "experiment_assignment_hash", "") or "").strip() or None,
        "policy_version": str(policy_json.get("policy_version") or "").strip() or None,
        "population_group": assignment_metadata.get("population_group"),
        "role": assignment_metadata.get("role"),
        "runtime_id": runtime_id,
        "model_id": assignment_metadata.get("model_id") or default_model_id,
    }
