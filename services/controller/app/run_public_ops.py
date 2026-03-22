"""Public run operations for the runtime/environment/run controller."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.catalog import candidate_environment_roots, get_environment
from app.config import settings
from app.database import (
    AgentActionEvent as AgentActionEventDB,
    Event as EventDB,
    Run as RunDB,
    RunAgentAssignment as RunAgentAssignmentDB,
    RunStatus as RunStatusEnum,
)
from app.models import RunStackRestartResponse
from app.run_binding import build_run_context
from app.run_config import coerce_int, coerce_optional_float
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
from app.telemetry_baseline import build_run_baseline_fields


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
    baseline_fields = build_run_baseline_fields(
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
        "population_group": baseline_fields.get("population_group"),
        "role": baseline_fields.get("role"),
        "agent_runtime_id": resolved_runtime_id,
        "agent_runtime_ids": assignment_runtime_ids,
        "model_id": resolved_model_id,
        "model_ids": assignment_model_ids,
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

    stop_result = run_launcher.stop_run(run_id)
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
        timeout=settings.health_check_timeout,
        interval=settings.health_check_interval,
    )
    if health_result.get("status") != "healthy":
        error_message = str(health_result.get("error") or "Unknown health-check error")
        await asyncio.to_thread(
            run_launcher.stop_run,
            run.run_id,
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

    delete_result = run_launcher.delete_run(run_id)
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
