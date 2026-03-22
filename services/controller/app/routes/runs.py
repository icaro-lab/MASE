"""Canonical runtime/environment/run routes."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.catalog import get_environment, get_runtime, validate_environment
from app.environment_hooks import launch_run_hooks, normalize_run_hooks
from app import run_compat
from app import run_binding
from app import run_read_model
from app.database import Run as RunDB, get_db
from app.models import RunCreate as LegacyRunCreate, RunStatus


router = APIRouter(prefix="/api/v1", tags=["runs"])
LEGACY_RUNTIME_ID_ALIASES = {
    "simplified-social": "openclaw",
    "openclaw-py": "openclaw",
}


class PopulationOverride(BaseModel):
    count: int | None = Field(default=None, ge=0)
    model: str | None = None


class RunCreateRequest(BaseModel):
    environment_id: str
    seed: int | None = None
    api_key: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    population_overrides: dict[str, PopulationOverride] = Field(default_factory=dict)


class RunSummaryResponse(BaseModel):
    run_id: str
    environment_id: str
    runtime_id: str | None = None
    status: str
    seed: int | None = None
    agent_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_seconds: int | None = None
    environment_url: str | None = None
    frontend_url: str | None = None
    snapshot_hash: str


class RunResponse(RunSummaryResponse):
    params: dict[str, Any] = Field(default_factory=dict)
    population_specs: dict[str, Any] = Field(default_factory=dict)
    launch: dict[str, Any] = Field(default_factory=dict)


class RunSnapshotResponse(BaseModel):
    run_id: str
    snapshot_hash: str
    environment_id: str
    runtime_id: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)


def _deep_merge_dicts(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_unique_strings(*raw_lists: Any) -> list[str]:
    values: list[str] = []
    for raw_list in raw_lists:
        if not isinstance(raw_list, list):
            continue
        for item in raw_list:
            text = str(item or "").strip()
            if not text or text in values:
                continue
            values.append(text)
    return values


def _normalize_runtime_id(value: Any) -> str | None:
    runtime_id = str(value or "").strip()
    if not runtime_id:
        return None
    return LEGACY_RUNTIME_ID_ALIASES.get(runtime_id, runtime_id)


def _publicize_launch(launch: dict[str, Any], environment_id: str) -> dict[str, Any]:
    public_launch = {key: value for key, value in launch.items() if key != "runtime_id"}
    public_launch["environment_id"] = environment_id
    return public_launch


def _publicize_population_specs(population_specs: dict[str, Any]) -> dict[str, Any]:
    public_specs: dict[str, Any] = {}
    for population_id, raw_spec in population_specs.items():
        spec = dict(raw_spec) if isinstance(raw_spec, dict) else {}
        runtime_id = str(spec.get("runtime_id") or "").strip()
        if runtime_id:
            spec["runtime_id"] = _normalize_runtime_id(runtime_id) or runtime_id
        public_specs[str(population_id)] = spec
    return public_specs


def _normalize_run_binding(run: RunDB, db: Session) -> dict[str, Any]:
    context = run_binding.build_run_context(run, db)
    env_config = context["environment_config"] if isinstance(context.get("environment_config"), dict) else {}
    launch = env_config.get("launch") if isinstance(env_config.get("launch"), dict) else {}
    launch_snapshot = {key: value for key, value in launch.items() if key != "hook_runs"}
    params = env_config.get("environment_params") if isinstance(env_config.get("environment_params"), dict) else {}
    population_specs = env_config.get("population_specs") if isinstance(env_config.get("population_specs"), dict) else {}
    run_hooks = env_config.get("run_hooks") if isinstance(env_config.get("run_hooks"), list) else []
    experiment_policy = (
        env_config.get("experiment_policy") if isinstance(env_config.get("experiment_policy"), dict) else {}
    )

    public_launch = _publicize_launch(launch_snapshot, context["environment_id"])
    public_population_specs = _publicize_population_specs(population_specs)

    snapshot = {
        "environment_id": context["environment_id"],
        "runtime_id": _normalize_runtime_id(context["runtime_id"]),
        "launch": public_launch,
        "params": params,
        "population_specs": public_population_specs,
        "run_hooks": run_hooks,
        "experiment_policy": experiment_policy,
        "runtime_controls": {
            "heartbeat": env_config.get("heartbeat"),
            "max_parallel_agents": env_config.get("max_parallel_agents"),
            "max_ticks": env_config.get("max_ticks"),
            "max_heartbeats_per_agent": env_config.get("max_heartbeats_per_agent"),
            "runtime_limit_minutes": env_config.get("runtime_limit_minutes"),
        },
        "seed": run.seed,
    }
    snapshot_hash = str(context.get("snapshot_hash") or "").strip()
    if not snapshot_hash:
        snapshot_hash = "sha256:" + hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    return {
        "environment_id": context["environment_id"],
        "runtime_id": _normalize_runtime_id(context["runtime_id"]),
        "launch": public_launch,
        "params": params,
        "population_specs": public_population_specs,
        "snapshot": snapshot,
        "snapshot_hash": snapshot_hash,
    }


def _serialize_run(run: RunDB, db: Session) -> RunResponse:
    binding = _normalize_run_binding(run, db)
    legacy = run_read_model.build_run_response(run, db)
    started_at = legacy.started_at
    ended_at = legacy.ended_at
    elapsed_seconds = None
    if started_at and ended_at:
        elapsed_seconds = int((ended_at - started_at).total_seconds())
    agent_count = int(legacy.agent_count or 0)
    if agent_count <= 0 and isinstance(binding.get("population_specs"), dict):
        agent_count = sum(
            int((spec or {}).get("count") or 0)
            for spec in binding["population_specs"].values()
            if isinstance(spec, dict)
        )

    return RunResponse(
        run_id=run.run_id,
        environment_id=binding["environment_id"],
        runtime_id=binding["runtime_id"],
        status=str(run.status),
        seed=run.seed,
        agent_count=agent_count,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_seconds=elapsed_seconds,
        environment_url=legacy.environment_url,
        frontend_url=legacy.frontend_url,
        snapshot_hash=binding["snapshot_hash"],
        params=binding["params"],
        population_specs=binding["population_specs"],
        launch=binding["launch"],
    )


def _serialize_run_summary(run: RunDB, db: Session) -> RunSummaryResponse:
    full = _serialize_run(run, db)
    return RunSummaryResponse(
        run_id=full.run_id,
        environment_id=full.environment_id,
        runtime_id=full.runtime_id,
        status=full.status,
        seed=full.seed,
        agent_count=full.agent_count,
        started_at=full.started_at,
        ended_at=full.ended_at,
        elapsed_seconds=full.elapsed_seconds,
        environment_url=full.environment_url,
        frontend_url=full.frontend_url,
        snapshot_hash=full.snapshot_hash,
    )

def _build_environment_config(
    environment_manifest: dict[str, Any],
    runtime_manifest: dict[str, Any],
    request: RunCreateRequest,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    launch = environment_manifest.get("launch") if isinstance(environment_manifest.get("launch"), dict) else {}
    environment_id = str(environment_manifest.get("id") or "").strip()
    if not environment_id:
        raise HTTPException(status_code=400, detail="Environment id is not defined")
    runtime_id = str(runtime_manifest.get("id") or "").strip() or "openclaw"

    populations = environment_manifest.get("populations")
    if not isinstance(populations, dict) or not populations:
        raise HTTPException(status_code=400, detail="Environment has no populations")

    population_specs: dict[str, Any] = {}
    total_agents = 0
    for raw_population_id, raw_population in populations.items():
        population_id = str(raw_population_id or "").strip()
        if not population_id or not isinstance(raw_population, dict):
            continue
        override = request.population_overrides.get(population_id)
        count = (
            override.count
            if override is not None and override.count is not None
            else int(raw_population.get("default_count") or 0)
        )
        if count <= 0:
            continue
        model_id = (
            str(override.model or "").strip()
            if override is not None and override.model is not None
            else str(raw_population.get("default_model") or "").strip()
        ) or "openai/gpt-5-mini"
        runtime_id_for_population = str(raw_population.get("runtime_id") or "").strip() or runtime_id
        population_specs[population_id] = {
            "count": count,
            "model_id": model_id,
            "runtime_id": runtime_id_for_population,
            "role_label": population_id,
        }
        total_agents += count

    if total_agents <= 0:
        raise HTTPException(status_code=400, detail="Resolved population count is zero")

    ordered_population_ids = sorted(population_specs.keys())
    default_population = population_specs[ordered_population_ids[0]]
    population_groups = {
        population_id: {
            "share": spec["count"] / total_agents,
            "runtime_id": spec["runtime_id"],
            "model_id": spec["model_id"],
            "role_label": spec["role_label"],
        }
        for population_id, spec in population_specs.items()
    }

    policy_manifest = environment_manifest.get("policy") if isinstance(environment_manifest.get("policy"), dict) else {}
    condition_id = str((request.params or {}).get("condition") or "").strip()
    condition_overrides = {}
    if condition_id:
        raw_conditions = policy_manifest.get("conditions")
        if isinstance(raw_conditions, dict):
            maybe_override = raw_conditions.get(condition_id)
            if isinstance(maybe_override, dict):
                condition_overrides = maybe_override

    base_policy_core = policy_manifest.get("core") if isinstance(policy_manifest.get("core"), dict) else {}
    override_policy_core = (
        condition_overrides.get("core") if isinstance(condition_overrides.get("core"), dict) else {}
    )
    resolved_policy_core = _deep_merge_dicts(base_policy_core, override_policy_core)
    required_capabilities = _merge_unique_strings(
        policy_manifest.get("required_capabilities"),
        condition_overrides.get("required_capabilities"),
        resolved_policy_core.get("required_capabilities"),
        ["population_mix"],
    )
    if required_capabilities:
        resolved_policy_core["required_capabilities"] = required_capabilities
    resolved_policy_core["population_groups"] = population_groups

    base_policy_env = policy_manifest.get("env") if isinstance(policy_manifest.get("env"), dict) else {}
    override_policy_env = (
        condition_overrides.get("env") if isinstance(condition_overrides.get("env"), dict) else {}
    )
    resolved_policy_env = _deep_merge_dicts(base_policy_env, override_policy_env)
    has_policy_controls = bool(policy_manifest) or bool(condition_overrides)

    runtime_defaults = (
        environment_manifest.get("runtime_defaults")
        if isinstance(environment_manifest.get("runtime_defaults"), dict)
        else {}
    )
    normalized_run_hooks = normalize_run_hooks(environment_manifest)

    env_config: dict[str, Any] = {
        "environment_id": str(environment_manifest.get("id") or "").strip(),
        "runtime_id": runtime_id,
        "launch": {
            "environment_id": environment_id,
            "pre_register_agents": bool(launch.get("pre_register_agents", True)),
        },
        "environment_params": dict(request.params or {}),
        "population_specs": population_specs,
        "agent_count": total_agents,
        "runtime_id": str(default_population["runtime_id"]),
        "runtime_source": "draft",
        "agent_model": str(default_population["model_id"]),
        "pre_register_agents": bool(launch.get("pre_register_agents", True)),
        "run_hooks": normalized_run_hooks,
    }
    if has_policy_controls:
        env_config["experiment_policy"] = {
            "policy_version": "1.0",
            "core": resolved_policy_core,
        }
        if resolved_policy_env:
            env_config["experiment_policy"]["env"] = resolved_policy_env
    for key in (
        "max_ticks",
        "max_heartbeats_per_agent",
        "runtime_limit_minutes",
        "max_parallel_agents",
        "agent_init_concurrency",
        "agent_init_timeout_seconds",
    ):
        if key in runtime_defaults:
            env_config[key] = runtime_defaults[key]
    heartbeat_defaults = runtime_defaults.get("heartbeat")
    if isinstance(heartbeat_defaults, dict) and heartbeat_defaults:
        env_config["heartbeat"] = copy.deepcopy(heartbeat_defaults)

    snapshot = {
        "environment_id": env_config["environment_id"],
        "runtime_id": env_config["runtime_id"],
        "launch": env_config["launch"],
        "params": env_config["environment_params"],
        "population_specs": population_specs,
        "run_hooks": normalized_run_hooks,
        "runtime_controls": {
            "heartbeat": env_config.get("heartbeat"),
            "max_parallel_agents": env_config.get("max_parallel_agents"),
            "max_ticks": env_config.get("max_ticks"),
            "max_heartbeats_per_agent": env_config.get("max_heartbeats_per_agent"),
            "runtime_limit_minutes": env_config.get("runtime_limit_minutes"),
        },
        "seed": request.seed,
    }
    if "experiment_policy" in env_config:
        snapshot["experiment_policy"] = env_config["experiment_policy"]
    return f"environment/{environment_id}", env_config, snapshot


@router.post("/runs", response_model=RunResponse)
async def create_run(request: RunCreateRequest, db: Session = Depends(get_db)) -> RunResponse:
    validation = validate_environment(request.environment_id)
    if not validation.get("valid"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_environment",
                "environment_id": request.environment_id,
                "errors": validation.get("errors", []),
                "warnings": validation.get("warnings", []),
            },
        )

    environment_manifest = get_environment(request.environment_id)
    if environment_manifest is None:
        raise HTTPException(status_code=404, detail=f"Environment not found: {request.environment_id}")

    runtime_id = str(environment_manifest.get("runtime") or "").strip()
    runtime_manifest = get_runtime(runtime_id)
    if runtime_manifest is None:
        raise HTTPException(status_code=400, detail=f"Runtime not found for environment: {runtime_id}")

    environment_ref, env_config, snapshot = _build_environment_config(
        environment_manifest,
        runtime_manifest,
        request,
    )
    legacy_run = await run_compat.create_bound_run(
        environment_ref=environment_ref,
        environment_config=env_config,
        snapshot=snapshot,
        request=LegacyRunCreate(
            seed=request.seed,
            api_key=request.api_key,
        ),
        db=db,
    )
    run = db.query(RunDB).filter(RunDB.run_id == legacy_run.run_id).first()
    if run is None:
        raise HTTPException(status_code=500, detail=f"Run {legacy_run.run_id} was created but not found")
    try:
        launched_hooks = launch_run_hooks(
            environment_manifest,
            trigger="on_run_start",
            run_payload=_serialize_run(run, db).model_dump(mode="json"),
            snapshot=snapshot,
        )
    except Exception as exc:
        await run_compat.stop_run(run.run_id, db=db)
        run = db.query(RunDB).filter(RunDB.run_id == run.run_id).first()
        if run is not None:
            run.status = RunStatus.FAILED.value
            db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to launch environment hooks: {exc}") from exc

    if launched_hooks:
        binding = run_binding.resolve_binding(run, db)
        if binding is not None and isinstance(binding.environment_config, dict):
            next_env_config = copy.deepcopy(binding.environment_config)
            launch_meta = next_env_config.get("launch") if isinstance(next_env_config.get("launch"), dict) else {}
            launch_meta["hook_runs"] = launched_hooks
            next_env_config["launch"] = launch_meta
            next_snapshot = copy.deepcopy(binding.snapshot) if isinstance(binding.snapshot, dict) else copy.deepcopy(snapshot)
            next_snapshot["launch"] = {
                key: value
                for key, value in launch_meta.items()
                if key != "hook_runs"
            }
            run_binding.upsert_run_binding(
                db,
                run=run,
                environment_id=str(binding.environment_id or "").strip() or request.environment_id,
                runtime_id=str(binding.runtime_id or "").strip() or None,
                environment_ref=str(binding.environment_ref or "").strip() or None,
                environment_config=next_env_config,
                snapshot=next_snapshot,
            )
            db.commit()
            db.refresh(binding)

    run = db.query(RunDB).filter(RunDB.run_id == legacy_run.run_id).first()
    if run is None:
        raise HTTPException(status_code=500, detail=f"Run {legacy_run.run_id} disappeared after hook launch")
    return _serialize_run(run, db)


@router.get("/runs", response_model=list[RunSummaryResponse])
async def list_runs(
    environment_id: str | None = Query(default=None),
    status: RunStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[RunSummaryResponse]:
    query = db.query(RunDB)
    if status is not None:
        query = query.filter(RunDB.status == status.value)
    runs = query.order_by(RunDB.started_at.desc(), RunDB.run_id.desc()).all()

    serialized = [_serialize_run_summary(run, db) for run in runs]
    if environment_id:
        serialized = [row for row in serialized if row.environment_id == environment_id]
    return serialized


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, db: Session = Depends(get_db)) -> RunResponse:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _serialize_run(run, db)


@router.get("/runs/{run_id}/snapshot", response_model=RunSnapshotResponse)
async def get_run_snapshot(run_id: str, db: Session = Depends(get_db)) -> RunSnapshotResponse:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    binding = _normalize_run_binding(run, db)
    return RunSnapshotResponse(
        run_id=run.run_id,
        snapshot_hash=binding["snapshot_hash"],
        environment_id=binding["environment_id"],
        runtime_id=binding["runtime_id"],
        snapshot=binding["snapshot"],
    )


@router.get("/runs/{run_id}/cost")
async def get_run_cost(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await run_compat.get_run_cost(run_id, db=db)


@router.get("/runs/{run_id}/scheduler/status")
async def get_run_scheduler_status(
    run_id: str,
    include_agent_states: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await run_compat.get_run_scheduler_status(
        run_id,
        include_agent_states=include_agent_states,
        db=db,
    )


@router.get("/runs/{run_id}/scheduler/progress")
async def get_run_scheduler_progress(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await run_compat.get_run_scheduler_progress(run_id, db=db)


@router.post("/runs/{run_id}/pause", response_model=RunResponse)
async def pause_run(run_id: str, db: Session = Depends(get_db)) -> RunResponse:
    await run_compat.pause_run(run_id, db=db)
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _serialize_run(run, db)


@router.post("/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(run_id: str, db: Session = Depends(get_db)) -> RunResponse:
    await run_compat.resume_run(run_id, db=db)
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _serialize_run(run, db)


@router.post("/runs/{run_id}/stop", response_model=RunResponse)
async def stop_run(run_id: str, db: Session = Depends(get_db)) -> RunResponse:
    await run_compat.stop_run(run_id, db=db)
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _serialize_run(run, db)


@router.post("/runs/{run_id}/restart-stack")
async def restart_run_stack(run_id: str, db: Session = Depends(get_db)):
    response = await run_compat.restart_run_stack(run_id, db=db)
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
    payload["run"] = _serialize_run(run, db).model_dump(mode="json")
    return payload


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    await run_compat.delete_run(run_id, db=db)
    return {"deleted": True, "run_id": run_id}
