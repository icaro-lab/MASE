"""Public run operations for the runtime/environment/run controller."""

from __future__ import annotations

import asyncio
import csv
from collections import defaultdict
from datetime import datetime, timezone
import io
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, Response
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.catalog import candidate_environment_roots, get_environment
from app.config import settings
from app.database import (
    AgentActionEvent as AgentActionEventDB,
    Event as EventDB,
    Run as RunDB,
    RunAgentAssignment as RunAgentAssignmentDB,
    RunEnvironmentAssignment as RunEnvironmentAssignmentDB,
    RunStatus as RunStatusEnum,
)
from app.models import RunStackRestartResponse
from app.run_binding import build_run_context
from app.run_config import coerce_int, coerce_optional_float, resolve_run_runtime_limit
from app.run_launcher import run_launcher
from app.run_read_model import (
    TERMINAL_RUN_STATUSES,
    as_utc_datetime,
    build_restart_launch_config,
    build_run_response,
    resolve_environment_id_for_run,
    resolve_run_service_urls,
)
from app.run_task_enforcement import (
    _resolve_agent_launcher_url,
    _scheduler_progress_for_run,
    _scheduler_status_for_run,
    cancel_run_max_agent_heartbeat_task,
    cancel_run_max_tick_task,
    cancel_run_runtime_limit_task,
    emit_run_terminal_event,
)
from app.telemetry_baseline import build_experiment_baseline_fields


logger = logging.getLogger(__name__)


def _environment_has_frontend(environment_id: str) -> bool:
    normalized = str(environment_id or "").strip()
    if not normalized:
        return False
    manifest = get_environment(normalized, candidate_environment_roots())
    if not isinstance(manifest, dict):
        return False
    launch = manifest.get("launch") if isinstance(manifest.get("launch"), dict) else {}
    frontend_service = launch.get("frontend_service")
    return isinstance(frontend_service, dict) and bool(str(frontend_service.get("service_name") or "").strip())


def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    normalized = as_utc_datetime(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def _extract_cost_limits(environment_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(environment_config, dict):
        return {}
    limits = environment_config.get("cost_limits") or environment_config.get("budget") or {}
    if not isinstance(limits, dict):
        return {}
    max_total = coerce_optional_float(
        limits.get("max_total_cost_usd")
        if "max_total_cost_usd" in limits
        else limits.get("max_cost_usd")
    )
    max_per_agent = coerce_optional_float(
        limits.get("max_cost_usd_per_agent")
        if "max_cost_usd_per_agent" in limits
        else limits.get("max_agent_cost_usd")
    )
    on_breach = str(limits.get("on_breach") or limits.get("breach_action") or "pause").strip().lower()
    if on_breach not in {"pause", "stop"}:
        on_breach = "pause"
    return {
        "max_total_cost_usd": max_total,
        "max_cost_usd_per_agent": max_per_agent,
        "on_breach": on_breach,
    }


def build_run_cost_payload(run: RunDB, db: Session) -> Dict[str, Any]:
    run_id = run.run_id
    context = build_run_context(run, db)
    cost_limits = _extract_cost_limits(context.get("environment_config"))
    baseline_fields = build_experiment_baseline_fields(
        db,
        run=run,
        agent_id=None,
    )
    assignment_rows = (
        db.query(
            RunAgentAssignmentDB.runtime_id.label("runtime_id"),
            RunAgentAssignmentDB.model_id.label("model_id"),
        )
        .filter(RunAgentAssignmentDB.run_id == run_id)
        .all()
    )
    assignment_runtime_ids = sorted(
        {
            str(getattr(row, "runtime_id", "") or "").strip()
            for row in assignment_rows
            if str(getattr(row, "runtime_id", "") or "").strip()
        }
    )
    assignment_model_ids = sorted(
        {
            str(getattr(row, "model_id", "") or "").strip()
            for row in assignment_rows
            if str(getattr(row, "model_id", "") or "").strip()
        }
    )
    resolved_runtime_id = baseline_fields.get("runtime_id")
    if assignment_runtime_ids:
        resolved_runtime_id = assignment_runtime_ids[0] if len(assignment_runtime_ids) == 1 else None
    resolved_model_id = baseline_fields.get("model_id")
    if assignment_model_ids:
        resolved_model_id = assignment_model_ids[0] if len(assignment_model_ids) == 1 else None

    totals_row = (
        db.query(
            func.coalesce(func.sum(AgentActionEventDB.llm_cost_usd), 0.0).label("llm_cost_usd"),
            func.coalesce(func.sum(AgentActionEventDB.ia_cost_usd), 0.0).label("ia_cost_usd"),
            func.coalesce(func.sum(AgentActionEventDB.llm_tokens_input), 0).label("llm_tokens_input"),
            func.coalesce(func.sum(AgentActionEventDB.llm_tokens_output), 0).label("llm_tokens_output"),
        )
        .filter(AgentActionEventDB.run_id == run_id)
        .one()
    )

    total_llm_cost = float(totals_row.llm_cost_usd or 0.0)
    total_ia_cost = float(totals_row.ia_cost_usd or 0.0)
    total_cost = total_llm_cost + total_ia_cost

    per_agent_rows = (
        db.query(
            AgentActionEventDB.agent_id.label("agent_id"),
            func.coalesce(func.sum(AgentActionEventDB.llm_cost_usd), 0.0).label("llm_cost_usd"),
            func.coalesce(func.sum(AgentActionEventDB.ia_cost_usd), 0.0).label("ia_cost_usd"),
            func.coalesce(func.sum(AgentActionEventDB.llm_tokens_input), 0).label("llm_tokens_input"),
            func.coalesce(func.sum(AgentActionEventDB.llm_tokens_output), 0).label("llm_tokens_output"),
        )
        .filter(AgentActionEventDB.run_id == run_id)
        .filter(AgentActionEventDB.agent_id.isnot(None))
        .group_by(AgentActionEventDB.agent_id)
        .order_by(AgentActionEventDB.agent_id.asc())
        .all()
    )

    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    dialect_name = getattr(dialect, "name", "")
    if dialect_name == "sqlite":
        bucket_expr = func.strftime("%Y-%m-%dT%H:%M:00", AgentActionEventDB.timestamp)
    else:
        bucket_expr = func.date_trunc("minute", AgentActionEventDB.timestamp)

    series_rows = (
        db.query(
            bucket_expr.label("bucket"),
            func.coalesce(func.sum(AgentActionEventDB.llm_cost_usd), 0.0).label("llm_cost_usd"),
            func.coalesce(func.sum(AgentActionEventDB.ia_cost_usd), 0.0).label("ia_cost_usd"),
        )
        .filter(AgentActionEventDB.run_id == run_id)
        .group_by(bucket_expr)
        .order_by(bucket_expr.asc())
        .all()
    )
    series_minute: List[Dict[str, Any]] = []
    for row in series_rows:
        bucket = row.bucket
        if isinstance(bucket, datetime):
            bucket_value = _to_utc_iso(bucket)
        elif hasattr(bucket, "isoformat"):
            bucket_value = str(bucket.isoformat())
        else:
            bucket_value = str(bucket)
        llm_cost = float(row.llm_cost_usd or 0.0)
        ia_cost = float(row.ia_cost_usd or 0.0)
        series_minute.append(
            {
                "bucket_start": bucket_value,
                "llm_cost_usd": llm_cost,
                "ia_cost_usd": ia_cost,
                "total_cost_usd": llm_cost + ia_cost,
            }
        )

    return {
        "run_id": run_id,
        "status": run.status,
        "policy_hash": baseline_fields.get("policy_hash"),
        "manifest_hash": baseline_fields.get("manifest_hash"),
        "assignment_hash": baseline_fields.get("assignment_hash"),
        "population_group": baseline_fields.get("population_group"),
        "role": baseline_fields.get("role"),
        "agent_runtime_id": resolved_runtime_id,
        "agent_runtime_ids": assignment_runtime_ids,
        "model_id": resolved_model_id,
        "model_ids": assignment_model_ids,
        "policy_version": baseline_fields.get("policy_version"),
        "cost_limits": cost_limits,
        "totals": {
            "llm_cost_usd": total_llm_cost,
            "ia_cost_usd": total_ia_cost,
            "total_cost_usd": total_cost,
            "llm_tokens_input": int(totals_row.llm_tokens_input or 0),
            "llm_tokens_output": int(totals_row.llm_tokens_output or 0),
        },
        "per_agent": [
            {
                "agent_id": row.agent_id,
                "llm_cost_usd": float(row.llm_cost_usd or 0.0),
                "ia_cost_usd": float(row.ia_cost_usd or 0.0),
                "total_cost_usd": float(row.llm_cost_usd or 0.0) + float(row.ia_cost_usd or 0.0),
                "llm_tokens_input": int(row.llm_tokens_input or 0),
                "llm_tokens_output": int(row.llm_tokens_output or 0),
            }
            for row in per_agent_rows
        ],
        "series_minute": series_minute[-240:],
    }


def _resolve_condition_manifest_mode(raw_mode: Optional[str]) -> str:
    mode = str(raw_mode or "strict").strip().lower() or "strict"
    if mode not in {"strict", "best_effort"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid condition manifest mode (expected: strict | best_effort)",
        )
    return mode


def _resolve_manifest_environment_ref(
    *,
    run: RunDB,
    env_assignment: Optional[RunEnvironmentAssignmentDB],
    db: Session,
) -> Optional[str]:
    environment_id = str(getattr(env_assignment, "environment_id", "") or "").strip() or None
    if environment_id:
        return f"environment/{environment_id}"

    fallback_environment = str(resolve_environment_id_for_run(run, db) or "").strip() or None
    if fallback_environment:
        return f"environment/{fallback_environment}"
    return None


def _extract_population_group_specs(env_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    policy_payload = env_config.get("experiment_policy")
    if not isinstance(policy_payload, dict):
        return {}
    core_payload = policy_payload.get("core")
    if not isinstance(core_payload, dict):
        return {}
    raw_groups = core_payload.get("population_groups")
    if not isinstance(raw_groups, dict):
        return {}

    specs: Dict[str, Dict[str, Any]] = {}
    for raw_group_name in sorted(raw_groups.keys(), key=lambda item: str(item)):
        group_name = str(raw_group_name or "").strip()
        if not group_name:
            continue
        payload = raw_groups.get(raw_group_name)
        if not isinstance(payload, dict):
            continue
        share = coerce_optional_float(payload.get("share"))
        specs[group_name] = {
            "share": share,
            "runtime_id": str(payload.get("runtime_id") or "").strip() or None,
            "model_id": str(payload.get("model_id") or "").strip() or None,
        }
    return specs


def _extract_compass_manifest_fields(env_config: Dict[str, Any]) -> Dict[str, Any]:
    policy_payload = env_config.get("experiment_policy")
    if not isinstance(policy_payload, dict):
        return {
            "instrument_version": None,
            "visibility_mode": None,
            "interval_minutes": None,
            "jitter_seconds": None,
            "grace_seconds": None,
            "refusal_policy": None,
        }
    env_payload = policy_payload.get("env")
    if not isinstance(env_payload, dict):
        env_payload = {}

    resolved_env_payload: Dict[str, Any] = {}
    for raw_value in env_payload.values():
        if isinstance(raw_value, dict):
            resolved_env_payload = raw_value
            break

    compass_payload = resolved_env_payload.get("compass") if isinstance(resolved_env_payload.get("compass"), dict) else None
    if compass_payload is None:
        legacy_payload = resolved_env_payload.get("compass_gate")
        if isinstance(legacy_payload, dict):
            compass_payload = legacy_payload
    if compass_payload is None:
        compass_payload = {}

    visibility_mode = str(compass_payload.get("visibility_mode") or "").strip() or None
    if visibility_mode:
        normalized_mode = visibility_mode.lower()
        visibility_mode = normalized_mode if normalized_mode in {"private", "public"} else None

    return {
        "instrument_version": str(compass_payload.get("instrument_version") or "").strip() or None,
        "visibility_mode": visibility_mode,
        "interval_minutes": coerce_int(compass_payload.get("interval_minutes"), default=0) or None,
        "jitter_seconds": coerce_int(compass_payload.get("jitter_seconds"), default=0),
        "grace_seconds": coerce_int(compass_payload.get("grace_seconds"), default=0),
        "refusal_policy": str(compass_payload.get("refusal_policy") or "").strip() or None,
    }


def _build_condition_manifest_population(
    *,
    run: RunDB,
    db: Session,
) -> Dict[str, Any]:
    context = build_run_context(run, db)
    env_config = context.get("environment_config") if isinstance(context.get("environment_config"), dict) else {}
    group_specs = _extract_population_group_specs(env_config)
    assignments = (
        db.query(RunAgentAssignmentDB)
        .filter(RunAgentAssignmentDB.run_id == run.run_id)
        .order_by(RunAgentAssignmentDB.runtime_agent_id.asc())
        .all()
    )

    group_counts: Dict[str, int] = defaultdict(int)
    group_runtime_by_id: Dict[str, Optional[str]] = {}
    group_model_by_id: Dict[str, Optional[str]] = {}
    for assignment in assignments:
        group_id = str(getattr(assignment, "population_group", "") or "").strip() or None
        if not group_id:
            continue
        group_counts[group_id] += 1
        runtime_id = str(getattr(assignment, "runtime_id", "") or "").strip() or None
        model_id = str(getattr(assignment, "model_id", "") or "").strip() or None
        if group_id not in group_runtime_by_id and runtime_id:
            group_runtime_by_id[group_id] = runtime_id
        if group_id not in group_model_by_id and model_id:
            group_model_by_id[group_id] = model_id

    group_ids = sorted(set(group_specs.keys()) | set(group_counts.keys()))
    groups: List[Dict[str, Any]] = []
    for group_id in group_ids:
        spec = group_specs.get(group_id) or {}
        groups.append(
            {
                "group_id": group_id,
                "share": spec.get("share"),
                "assigned_count": int(group_counts.get(group_id, 0)),
                "runtime_id": group_runtime_by_id.get(group_id) or spec.get("runtime_id"),
                "model_id": group_model_by_id.get(group_id) or spec.get("model_id"),
            }
        )

    configured_agent_count = coerce_int(env_config.get("agent_count"), default=0) if isinstance(env_config, dict) else 0
    agent_count = len(assignments) if assignments else max(configured_agent_count, 0)
    return {
        "agent_count": max(int(agent_count), 0),
        "groups": groups,
    }


def build_condition_manifest_payload(run: RunDB, db: Session, *, mode: str) -> Dict[str, Any]:
    context = build_run_context(run, db)

    env_assignment = (
        db.query(RunEnvironmentAssignmentDB)
        .filter(RunEnvironmentAssignmentDB.run_id == run.run_id)
        .first()
    )
    runtime_limit_minutes, _ = resolve_run_runtime_limit(context.get("environment_config"))
    population = _build_condition_manifest_population(run=run, db=db)
    env_config = context.get("environment_config") if isinstance(context.get("environment_config"), dict) else {}
    compass_fields = _extract_compass_manifest_fields(env_config)

    policy_hash = str(getattr(run, "experiment_policy_hash", "") or "").strip() or None
    manifest_hash = str(getattr(run, "experiment_manifest_hash", "") or "").strip() or None
    assignment_hash = str(getattr(run, "experiment_assignment_hash", "") or "").strip() or None
    missing_fields = [
        field_name
        for field_name, value in (
            ("policy_hash", policy_hash),
            ("manifest_hash", manifest_hash),
            ("assignment_hash", assignment_hash),
        )
        if not value
    ]

    resolved_mode = _resolve_condition_manifest_mode(mode)
    if resolved_mode == "strict" and missing_fields:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "condition_manifest_unavailable",
                "message": "Run does not contain required pinned condition fields",
                "missing_fields": missing_fields,
            },
        )

    source = "backfill" if missing_fields else "native"
    payload: Dict[str, Any] = {
        "run_id": run.run_id,
        "environment_ref": _resolve_manifest_environment_ref(
            run=run,
            env_assignment=env_assignment,
            db=db,
        ),
        "hashes": {
            "resolved_bundle_hash": str(getattr(run, "resolved_bundle_hash", "") or "").strip() or None,
            "policy_hash": policy_hash,
            "manifest_hash": manifest_hash,
            "assignment_hash": assignment_hash,
        },
        "seed": run.seed,
        "population": population,
        "runtime": {
            "runtime_limit_minutes": runtime_limit_minutes,
        },
        "compass": compass_fields,
        "timestamps": {
            "run_started_at": _to_utc_iso(run.started_at),
            "run_ended_at": _to_utc_iso(run.ended_at),
        },
        "source": source,
    }
    if missing_fields:
        payload["missing_fields"] = missing_fields
    return payload


def serialize_condition_manifest_csv(manifest: Dict[str, Any]) -> str:
    columns = [
        "run_id",
        "source",
        "environment_ref",
        "resolved_bundle_hash",
        "policy_hash",
        "manifest_hash",
        "assignment_hash",
        "seed",
        "runtime_limit_minutes",
        "compass_instrument_version",
        "compass_visibility_mode",
        "compass_interval_minutes",
        "compass_jitter_seconds",
        "compass_grace_seconds",
        "compass_refusal_policy",
        "run_started_at",
        "run_ended_at",
        "population_agent_count",
        "group_id",
        "group_share",
        "group_assigned_count",
        "group_runtime_id",
        "group_model_id",
    ]

    hashes = manifest.get("hashes") if isinstance(manifest.get("hashes"), dict) else {}
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    compass = manifest.get("compass") if isinstance(manifest.get("compass"), dict) else {}
    timestamps = manifest.get("timestamps") if isinstance(manifest.get("timestamps"), dict) else {}
    population = manifest.get("population") if isinstance(manifest.get("population"), dict) else {}
    groups = population.get("groups") if isinstance(population.get("groups"), list) else []
    sorted_groups = sorted(
        [group for group in groups if isinstance(group, dict)],
        key=lambda row: str(row.get("group_id") or ""),
    )

    base_row = {
        "run_id": manifest.get("run_id"),
        "source": manifest.get("source"),
        "environment_ref": manifest.get("environment_ref"),
        "resolved_bundle_hash": hashes.get("resolved_bundle_hash"),
        "policy_hash": hashes.get("policy_hash"),
        "manifest_hash": hashes.get("manifest_hash"),
        "assignment_hash": hashes.get("assignment_hash"),
        "seed": manifest.get("seed"),
        "runtime_limit_minutes": runtime.get("runtime_limit_minutes"),
        "compass_instrument_version": compass.get("instrument_version"),
        "compass_visibility_mode": compass.get("visibility_mode"),
        "compass_interval_minutes": compass.get("interval_minutes"),
        "compass_jitter_seconds": compass.get("jitter_seconds"),
        "compass_grace_seconds": compass.get("grace_seconds"),
        "compass_refusal_policy": compass.get("refusal_policy"),
        "run_started_at": timestamps.get("run_started_at"),
        "run_ended_at": timestamps.get("run_ended_at"),
        "population_agent_count": population.get("agent_count"),
    }

    if sorted_groups:
        rows = [
            {
                **base_row,
                "group_id": group.get("group_id"),
                "group_share": group.get("share"),
                "group_assigned_count": group.get("assigned_count"),
                "group_runtime_id": group.get("runtime_id"),
                "group_model_id": group.get("model_id"),
            }
            for group in sorted_groups
        ]
    else:
        rows = [
            {
                **base_row,
                "group_id": None,
                "group_share": None,
                "group_assigned_count": None,
                "group_runtime_id": None,
                "group_model_id": None,
            }
        ]

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in columns})
    return buffer.getvalue()


async def fetch_run_compass_review_payload(
    run: RunDB,
    db: Session,
    *,
    history_limit: int,
    event_limit: int,
) -> Dict[str, Any]:
    service_urls = resolve_run_service_urls(run, db)
    environment_id = resolve_environment_id_for_run(run, db)
    environment_url = str(
        service_urls.get("environment")
        or settings.get_environment_url(run.run_id, environment_id=environment_id)
        or ""
    ).strip()
    if not environment_url:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run.run_id} does not have a resolved environment URL",
        )

    params = {
        "run_id": run.run_id,
        "history_limit": history_limit,
        "event_limit": event_limit,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{environment_url}/compass/review", params=params)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Compass review request timed out for run {run.run_id}",
        ) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Environment unavailable for run {run.run_id}",
        ) from exc

    try:
        detail: Any = response.json()
    except Exception:
        detail = response.text or response.reason_phrase or "Compass review request failed"

    if response.status_code >= 400:
        if response.status_code in {400, 409, 422}:
            raise HTTPException(status_code=response.status_code, detail=detail)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "compass_review_unavailable",
                "message": f"Environment compass review failed with {response.status_code}",
                "upstream_detail": detail,
            },
        )
    if not isinstance(detail, dict):
        raise HTTPException(
            status_code=502,
            detail="Environment compass review returned a non-object payload",
        )

    detail["environment_url"] = environment_url
    launch = context.get("launch") if isinstance(context.get("launch"), dict) else {}
    frontend_service = launch.get("frontend_service") if isinstance(launch.get("frontend_service"), dict) else None
    detail["frontend_url"] = None
    if frontend_service or _environment_has_frontend(str(detail.get("environment_id") or "")):
        detail["frontend_url"] = (
            str(service_urls.get("environment_frontend") or "").strip()
            or run_launcher.get_environment_frontend_url(run.run_id)
        )
    detail["run_status"] = run.status
    return detail


def _safe_json_loads(raw: Any) -> Optional[Any]:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _looks_env_read(path_or_url: Optional[str], method: Optional[str]) -> bool:
    if (method or "").upper() != "GET":
        return False
    target = str(path_or_url or "").lower()
    if not target:
        return False
    read_markers = [
        "/feed",
        "/posts",
        "/comments",
        "/submolts",
        "/search",
        "/agents/me",
        "/agents/profile",
    ]
    return any(marker in target for marker in read_markers)


def _extract_observed_items(path_or_url: Optional[str], response_preview: Optional[str]) -> List[Dict[str, str]]:
    target = str(path_or_url or "")
    parsed = _safe_json_loads(response_preview)
    if parsed is None:
        text = (response_preview or "").strip()
        if not text:
            return []
        return [{"source": target, "label": text[:220]}]

    rows: List[Dict[str, str]] = []
    items: List[Any] = []
    if isinstance(parsed, list):
        items = parsed[:5]
    elif isinstance(parsed, dict):
        for key in ("posts", "results", "comments", "data", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                items = value[:5]
                break
        if not items:
            items = [parsed]

    for item in items:
        if isinstance(item, dict):
            label = (
                item.get("title")
                or item.get("content")
                or item.get("name")
                or item.get("id")
                or json.dumps(item, ensure_ascii=True)
            )
        else:
            label = str(item)
        rows.append({"source": target, "label": str(label)[:220]})
    return rows[:8]


async def get_run_agent_context(
    run_id: str,
    *,
    limit_per_agent: int,
    db: Session,
) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    heartbeat_counts: Dict[str, int] = {}
    heartbeat_rows = (
        db.query(
            AgentActionEventDB.agent_id.label("runtime_agent_id"),
            func.count(AgentActionEventDB.event_id).label("heartbeat_count"),
        )
        .filter(AgentActionEventDB.run_id == run_id)
        .filter(AgentActionEventDB.agent_id.isnot(None))
        .filter(AgentActionEventDB.action_type == "heartbeat")
        .group_by(AgentActionEventDB.agent_id)
        .all()
    )
    heartbeat_counts = {
        str(getattr(row, "runtime_agent_id", "") or "").strip(): coerce_int(
            getattr(row, "heartbeat_count", 0),
            default=0,
        )
        for row in heartbeat_rows
        if str(getattr(row, "runtime_agent_id", "") or "").strip()
    }

    events = (
        db.query(AgentActionEventDB)
        .filter(AgentActionEventDB.run_id == run_id)
        .filter(AgentActionEventDB.agent_id.isnot(None))
        .order_by(AgentActionEventDB.timestamp.desc())
        .limit(4000)
        .all()
    )

    by_agent: Dict[str, Dict[str, Any]] = {}
    for event in events:
        agent_id = str(event.agent_id or "").strip()
        if not agent_id:
            continue
        payload = event.payload or {}
        event_type = str(payload.get("event_type") or event.action_type or "")
        if agent_id not in by_agent:
            by_agent[agent_id] = {
                "agent_id": agent_id,
                "latest_tick": None,
                "heartbeat_count": heartbeat_counts.get(agent_id, 0),
                "latest_model": None,
                "latest_user_message": None,
                "latest_prompt_contract_version": None,
                "latest_interaction_mode": None,
                "latest_policy_source": None,
                "latest_memory_mode": None,
                "latest_memory_turns_loaded": 0,
                "prompt_parts_static": {},
                "prompt_parts_dynamic": {},
                "recent_reads": [],
                "recent_writes": [],
                "recent_heartbeats": [],
                "latest_heartbeat": None,
                "observed_items": [],
                "totals": {
                    "reads": 0,
                    "writes": 0,
                    "errors": 0,
                    "env_reads": 0,
                    "env_writes": 0,
                    "self_writes": 0,
                    "gated_skips": 0,
                },
            }

        row = by_agent[agent_id]
        if event_type == "llm_io":
            tick = coerce_int(payload.get("tick"), default=-1)
            if tick >= 0:
                previous_tick = row.get("latest_tick")
                if previous_tick is None:
                    row["latest_tick"] = tick
                else:
                    row["latest_tick"] = max(coerce_int(previous_tick, default=-1), tick)
            if not row["latest_model"]:
                row["latest_model"] = payload.get("model")
            if not row["latest_user_message"]:
                row["latest_user_message"] = payload.get("user_message")
            if not row["latest_prompt_contract_version"]:
                row["latest_prompt_contract_version"] = payload.get("prompt_contract_version")
            if not row["latest_interaction_mode"]:
                row["latest_interaction_mode"] = payload.get("interaction_mode")
            if not row["latest_policy_source"]:
                row["latest_policy_source"] = payload.get("policy_source")
            if not row["latest_memory_mode"]:
                row["latest_memory_mode"] = payload.get("memory_mode")
            memory_turns_loaded = coerce_int(payload.get("memory_turns_loaded"), default=0)
            if memory_turns_loaded > coerce_int(row.get("latest_memory_turns_loaded"), default=0):
                row["latest_memory_turns_loaded"] = memory_turns_loaded
            for part in payload.get("system_prompt_parts") or []:
                if not isinstance(part, dict):
                    continue
                sha = part.get("sha256")
                name = part.get("name")
                kind = part.get("kind")
                dynamic = bool(part.get("dynamic"))
                part_entry = {
                    "name": name,
                    "kind": kind,
                    "sha256": sha,
                    "dynamic": dynamic,
                }
                if dynamic:
                    dynamic_key = f"{name or 'part'}:{kind or 'dynamic'}"
                    row["prompt_parts_dynamic"][dynamic_key] = part_entry
                else:
                    static_key = str(sha or f"{name}:{kind}")
                    if static_key not in row["prompt_parts_static"]:
                        row["prompt_parts_static"][static_key] = part_entry

        if event_type == "heartbeat_result":
            if not row["latest_prompt_contract_version"]:
                row["latest_prompt_contract_version"] = payload.get("prompt_contract_version")
            if not row["latest_interaction_mode"]:
                row["latest_interaction_mode"] = payload.get("interaction_mode")
            if not row["latest_policy_source"]:
                row["latest_policy_source"] = payload.get("policy_source")
            if not row["latest_memory_mode"]:
                row["latest_memory_mode"] = payload.get("memory_mode")
            heartbeat_memory_turns_loaded = coerce_int(payload.get("memory_turns_loaded"), default=0)
            if heartbeat_memory_turns_loaded > coerce_int(row.get("latest_memory_turns_loaded"), default=0):
                row["latest_memory_turns_loaded"] = heartbeat_memory_turns_loaded
            stop_reason = payload.get("stop_reason")
            heartbeat_status = payload.get("heartbeat_status")
            heartbeat_entry = {
                "timestamp": _to_utc_iso(event.timestamp),
                "tick": payload.get("tick"),
                "heartbeat_status": heartbeat_status,
                "stop_reason": stop_reason,
                "rounds_executed": coerce_int(payload.get("rounds_executed"), default=0),
                "total_model_calls": coerce_int(payload.get("total_model_calls"), default=0),
                "elapsed_ms": coerce_int(payload.get("elapsed_ms"), default=0),
                "gate": payload.get("gate") if isinstance(payload.get("gate"), dict) else None,
            }
            if row["latest_heartbeat"] is None:
                row["latest_heartbeat"] = heartbeat_entry
            if len(row["recent_heartbeats"]) < limit_per_agent:
                row["recent_heartbeats"].append(heartbeat_entry)
            if (
                str(stop_reason or "").strip().lower() == "gated_skip"
                or str(heartbeat_status or "").strip().lower() == "skipped"
            ):
                row["totals"]["gated_skips"] += 1

        if event_type != "action_attempt":
            continue

        method = str(payload.get("method") or "").upper()
        action_type = str(payload.get("action_type") or "").lower()
        path_or_url = payload.get("url") or payload.get("path")
        status_code = payload.get("status_code")
        entry = {
            "timestamp": _to_utc_iso(event.timestamp),
            "tick": payload.get("tick"),
            "method": method,
            "path_or_url": path_or_url,
            "status_code": status_code,
            "action_name": payload.get("action_name"),
            "error_code": payload.get("error_code"),
            "response_preview": payload.get("response_preview"),
        }

        if isinstance(status_code, int) and status_code >= 400:
            row["totals"]["errors"] += 1

        if method == "GET":
            row["totals"]["reads"] += 1
            row["totals"]["env_reads"] += 1
            if len(row["recent_reads"]) < limit_per_agent:
                row["recent_reads"].append(entry)
            if _looks_env_read(path_or_url, method):
                observed = _extract_observed_items(path_or_url, payload.get("response_preview"))
                remaining = max(0, limit_per_agent - len(row["observed_items"]))
                if remaining > 0:
                    row["observed_items"].extend(observed[:remaining])
        elif method in {"POST", "PUT", "PATCH", "DELETE"}:
            row["totals"]["writes"] += 1
            row["totals"]["env_writes"] += 1
            if len(row["recent_writes"]) < limit_per_agent:
                row["recent_writes"].append(entry)
        elif action_type.startswith("fs_"):
            row["totals"]["self_writes"] += 1

    agents = []
    for row in by_agent.values():
        prompt_parts = list(row["prompt_parts_static"].values()) + list(row["prompt_parts_dynamic"].values())
        row["prompt_parts"] = sorted(
            prompt_parts,
            key=lambda part: (
                str(part.get("kind") or ""),
                str(part.get("name") or ""),
                str(part.get("sha256") or ""),
            ),
        )
        row.pop("prompt_parts_static", None)
        row.pop("prompt_parts_dynamic", None)
        agents.append(row)

    agents.sort(key=lambda item: item["agent_id"])
    return {
        "run_id": run_id,
        "generated_at": _to_utc_iso(datetime.utcnow()),
        "agent_count": len(agents),
        "agents": agents,
    }


async def export_agent_context(
    run_id: str,
    agent_id: str,
    *,
    tick: Optional[int],
    db: Session,
) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    query = (
        db.query(AgentActionEventDB)
        .filter(AgentActionEventDB.run_id == run_id)
        .filter(AgentActionEventDB.agent_id == agent_id)
        .filter(AgentActionEventDB.action_type == "llm_io")
        .order_by(AgentActionEventDB.timestamp.asc())
    )
    llm_events = query.all()

    rounds: List[Dict[str, Any]] = []
    for event in llm_events:
        payload = event.payload or {}
        event_tick = coerce_int(payload.get("tick"), default=-1)
        if tick is not None and event_tick > tick:
            continue
        parts = payload.get("system_prompt_parts") or []
        normalized_parts: List[Dict[str, Any]] = []
        rendered_chunks: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            content = str(part.get("content") or "")
            normalized_parts.append(
                {
                    "name": part.get("name"),
                    "kind": part.get("kind"),
                    "dynamic": bool(part.get("dynamic")),
                    "sha256": part.get("sha256"),
                    "content": content,
                }
            )
            if content:
                rendered_chunks.append(content)

        rounds.append(
            {
                "timestamp": _to_utc_iso(event.timestamp),
                "tick": event_tick,
                "heartbeat_index": coerce_int(payload.get("heartbeat_index"), default=0),
                "round_index": coerce_int(payload.get("round_index"), default=0),
                "model": payload.get("model"),
                "system_prompt_parts": normalized_parts,
                "system_prompt": "\n\n".join(chunk for chunk in rendered_chunks if chunk),
                "user_message": payload.get("user_message"),
                "observation_message": payload.get("observation_message"),
                "assistant_response": payload.get("response_text"),
                "actions": payload.get("actions") or [],
                "observations": payload.get("observations") or [],
                "memory_mode": payload.get("memory_mode"),
                "memory_turns_loaded": coerce_int(payload.get("memory_turns_loaded"), default=0),
            }
        )

    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "upto_tick": tick,
        "generated_at": _to_utc_iso(datetime.utcnow()),
        "round_count": len(rounds),
        "rounds": rounds,
    }


async def _scheduler_control_for_run(
    run_id: str,
    action: str,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=400, detail=f"Unsupported scheduler action: {action}")
    scheduler_url = f"{_resolve_agent_launcher_url(run_id, db=db)}/scheduler/{action}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(scheduler_url)
            if response.status_code in (200, 201):
                return response.json() if response.text else {"status": action}
            if response.status_code == 400:
                payload = response.json() if response.text else {}
                detail = str(payload.get("detail") or "").lower()
                if action == "pause" and "state: paused" in detail:
                    return {"status": "already_paused"}
                if action == "resume" and "state: running" in detail:
                    return {"status": "already_running"}
            raise HTTPException(
                status_code=502,
                detail=f"Scheduler {action} failed for run {run_id}: {response.status_code} {response.text}",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to reach scheduler for run {run_id}: {str(exc)}",
            ) from exc


async def get_run_scheduler_status(
    run_id: str,
    *,
    include_agent_states: bool = False,
    db: Session,
) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run_status = str(run.status or "")
    if run_status in TERMINAL_RUN_STATUSES:
        return {
            "run_id": run_id,
            "run_status": run_status,
            "state": "stopped",
            "tick_count": 0,
            "start_time": None,
            "uptime_seconds": 0.0,
            "config": {},
            "agents": {},
            "reachable": False,
            "message": f"Run is {run_status}; scheduler is not expected to be active.",
        }

    status = await _scheduler_status_for_run(run_id, db=db, include_agent_states=include_agent_states)
    return {
        "run_id": run_id,
        "run_status": run_status,
        "state": str(status.get("state") or "unknown"),
        "tick_count": coerce_int(status.get("tick_count"), 0),
        "start_time": status.get("start_time"),
        "uptime_seconds": coerce_optional_float(status.get("uptime_seconds")) or 0.0,
        "config": status.get("config") if isinstance(status.get("config"), dict) else {},
        "agents": status.get("agents") if isinstance(status.get("agents"), dict) else {},
        "agent_states": status.get("agent_states") if include_agent_states and isinstance(status.get("agent_states"), list) else [],
        "reachable": True,
    }


async def get_run_scheduler_progress(run_id: str, *, db: Session) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run_status = str(run.status or "")
    if run_status in TERMINAL_RUN_STATUSES:
        return {
            "run_id": run_id,
            "run_status": run_status,
            "state": "stopped",
            "tick_count": 0,
            "start_time": None,
            "uptime_seconds": 0.0,
            "config": {},
            "agents": {},
            "progress": {},
            "reachable": False,
            "message": f"Run is {run_status}; scheduler is not expected to be active.",
        }

    progress = await _scheduler_progress_for_run(run_id, db=db)
    return {
        "run_id": run_id,
        "run_status": run_status,
        "state": str(progress.get("state") or "unknown"),
        "tick_count": coerce_int(progress.get("tick_count"), 0),
        "start_time": progress.get("start_time"),
        "uptime_seconds": coerce_optional_float(progress.get("uptime_seconds")) or 0.0,
        "config": progress.get("config") if isinstance(progress.get("config"), dict) else {},
        "agents": progress.get("agents") if isinstance(progress.get("agents"), dict) else {},
        "progress": progress.get("progress") if isinstance(progress.get("progress"), dict) else {},
        "reachable": True,
    }


async def pause_run(run_id: str, *, db: Session):
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status == RunStatusEnum.PAUSED.value:
        return build_run_response(run, db)
    if run.status != RunStatusEnum.RUNNING.value:
        raise HTTPException(status_code=409, detail=f"Run {run_id} cannot be paused from state {run.status}")

    await _scheduler_control_for_run(run_id, "pause", db=db)
    run.status = RunStatusEnum.PAUSED.value
    db.commit()
    db.refresh(run)
    return build_run_response(run, db)


async def resume_run(run_id: str, *, db: Session):
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status == RunStatusEnum.RUNNING.value:
        return build_run_response(run, db)
    if run.status != RunStatusEnum.PAUSED.value:
        raise HTTPException(status_code=409, detail=f"Run {run_id} cannot be resumed from state {run.status}")

    await _scheduler_control_for_run(run_id, "resume", db=db)
    run.status = RunStatusEnum.RUNNING.value
    db.commit()
    db.refresh(run)
    return build_run_response(run, db)


async def stop_run(run_id: str, *, db: Session):
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status not in (
        RunStatusEnum.PENDING.value,
        RunStatusEnum.RUNNING.value,
        RunStatusEnum.PAUSED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not stoppable from status {run.status}",
        )

    stop_result = run_launcher.stop_run(run_id, institutional_mode=run.institutional_mode)
    if stop_result.get("status") != "success":
        status = str(stop_result.get("status") or "").strip().lower()
        error_text = str(stop_result.get("error") or "").lower()
        if status != "not_found" and "not found" not in error_text:
            error_message = stop_result.get("error", "Unknown error")
            logger.error("stop_run container_stop_failed run_id=%s error=%s", run_id, error_message)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to stop run containers for {run_id}: {error_message}",
            )

    prior_status = str(run.status or "")
    run.status = RunStatusEnum.CANCELLED.value
    run.ended_at = datetime.utcnow()
    emit_run_terminal_event(
        db=db,
        run=run,
        terminal_status=RunStatusEnum.CANCELLED.value,
        terminal_reason="operator_stop_pending" if prior_status == RunStatusEnum.PENDING.value else "operator_stop",
        terminal_source="stop_run_route",
    )
    db.commit()
    db.refresh(run)
    cancel_run_runtime_limit_task(run_id)
    cancel_run_max_tick_task(run_id)
    cancel_run_max_agent_heartbeat_task(run_id)
    return build_run_response(run, db)


async def restart_run_stack(run_id: str, *, db: Session) -> RunStackRestartResponse:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run_status = str(run.status or "")
    if run_status not in TERMINAL_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run_id} is {run_status} and cannot use stack restart; "
                "use pause/resume or live URLs instead"
            ),
        )

    environment_id = resolve_environment_id_for_run(run, db)
    current_stack = await asyncio.to_thread(run_launcher.get_run_status, run_id)
    current_stack_status = str(current_stack.get("status") or "").strip().lower()
    if current_stack_status == "error":
        error_message = str(current_stack.get("error") or "Unknown orchestrator status error")
        raise HTTPException(
            status_code=503,
            detail=f"Unable to inspect current stack state for {run_id}: {error_message}",
        )

    if current_stack_status in {"running", "healthy"}:
        service_urls = resolve_run_service_urls(run, db)
        return RunStackRestartResponse(
            run=build_run_response(run, db),
            service_urls=service_urls,
            stack_status="already_running",
            restarted_at=datetime.utcnow().replace(tzinfo=timezone.utc),
        )

    launch_config = build_restart_launch_config(run, db)
    launch_result = await asyncio.to_thread(run_launcher.launch_run, launch_config)
    if launch_result.get("status") != "launched":
        launch_error = launch_result.get("error", "Unknown error")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to relaunch run stack for {run_id}: {launch_error}",
        )

    health_result = await run_launcher.wait_for_run_services(
        run_id=run.run_id,
        environment_id=environment_id,
        institutional_mode=bool(run.institutional_mode),
        timeout=settings.health_check_timeout,
        interval=settings.health_check_interval,
    )
    if health_result.get("status") != "healthy":
        error_message = str(health_result.get("error") or "Unknown health-check error")
        await asyncio.to_thread(
            run_launcher.stop_run,
            run.run_id,
            institutional_mode=bool(run.institutional_mode),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Relaunched stack for {run_id} failed health checks: {error_message}",
        )

    service_urls = (
        health_result.get("service_urls")
        if isinstance(health_result.get("service_urls"), dict)
        else resolve_run_service_urls(run, db)
    )
    return RunStackRestartResponse(
        run=build_run_response(run, db),
        service_urls=service_urls,
        stack_status="relaunched",
        restarted_at=datetime.utcnow().replace(tzinfo=timezone.utc),
    )


async def delete_run(run_id: str, *, db: Session) -> None:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status in (RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value):
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} must be stopped before deletion (current: {run.status})",
        )

    delete_result = run_launcher.delete_run(run_id, institutional_mode=run.institutional_mode)
    if delete_result.get("status") != "success":
        logger.warning(
            "delete_run container_delete_failed run_id=%s error=%s",
            run_id,
            delete_result.get("error", "Unknown error"),
        )

    cancel_run_runtime_limit_task(run_id)
    cancel_run_max_tick_task(run_id)
    cancel_run_max_agent_heartbeat_task(run_id)
    db.delete(run)
    db.commit()


async def get_run_cost(run_id: str, *, db: Session) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return build_run_cost_payload(run, db)


async def get_run_compass_review(
    run_id: str,
    *,
    history_limit: int,
    event_limit: int,
    db: Session,
) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return await fetch_run_compass_review_payload(
        run,
        db,
        history_limit=history_limit,
        event_limit=event_limit,
    )


async def get_run_condition_manifest(run_id: str, *, mode: str, db: Session) -> Dict[str, Any]:
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return build_condition_manifest_payload(run, db, mode=mode)


async def get_run_condition_manifest_csv(run_id: str, *, mode: str, db: Session):
    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    manifest = build_condition_manifest_payload(run, db, mode=mode)
    csv_content = serialize_condition_manifest_csv(manifest)
    filename = f"{run_id}-condition-manifest.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
