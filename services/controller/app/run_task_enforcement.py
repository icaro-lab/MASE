"""Controller-owned run task recovery and enforcement helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import (
    Event as EventDB,
    Run as RunDB,
    RunAgentAssignment as RunAgentAssignmentDB,
    RunStatus as RunStatusEnum,
    SessionLocal,
)
from app.run_binding import build_run_context
from app.run_config import (
    coerce_int,
    coerce_optional_float,
    resolve_run_max_heartbeats_per_agent,
    resolve_run_max_ticks,
    resolve_run_runtime_limit,
)
from app.run_launcher import run_launcher
from app.run_read_model import (
    TERMINAL_RUN_STATUSES,
    as_utc_datetime,
    resolve_run_service_urls,
)
from app.telemetry_baseline import build_experiment_baseline_fields


logger = logging.getLogger(__name__)
RUN_RUNTIME_LIMIT_TASKS: dict[str, asyncio.Task[Any]] = {}
RUN_MAX_TICK_TASKS: dict[str, asyncio.Task[Any]] = {}
RUN_MAX_AGENT_HEARTBEAT_TASKS: dict[str, asyncio.Task[Any]] = {}


def _to_utc_iso(value: datetime | None) -> str | None:
    normalized = as_utc_datetime(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def cancel_run_runtime_limit_task(run_id: str) -> None:
    existing = RUN_RUNTIME_LIMIT_TASKS.pop(run_id, None)
    if existing and not existing.done():
        existing.cancel()


def cancel_run_max_tick_task(run_id: str) -> None:
    existing = RUN_MAX_TICK_TASKS.pop(run_id, None)
    if existing and not existing.done():
        existing.cancel()


def cancel_run_max_agent_heartbeat_task(run_id: str) -> None:
    existing = RUN_MAX_AGENT_HEARTBEAT_TASKS.pop(run_id, None)
    if existing and not existing.done():
        existing.cancel()


def cancel_all_run_runtime_limit_tasks() -> int:
    run_ids = list(RUN_RUNTIME_LIMIT_TASKS.keys())
    for run_id in run_ids:
        cancel_run_runtime_limit_task(run_id)
    return len(run_ids)


def cancel_all_run_max_tick_tasks() -> int:
    run_ids = list(RUN_MAX_TICK_TASKS.keys())
    for run_id in run_ids:
        cancel_run_max_tick_task(run_id)
    return len(run_ids)


def cancel_all_run_max_agent_heartbeat_tasks() -> int:
    run_ids = list(RUN_MAX_AGENT_HEARTBEAT_TASKS.keys())
    for run_id in run_ids:
        cancel_run_max_agent_heartbeat_task(run_id)
    return len(run_ids)


def _compute_run_runtime_limit_deadline(
    started_at: datetime | None,
    runtime_limit_minutes: int,
) -> datetime | None:
    if started_at is None or runtime_limit_minutes <= 0:
        return None
    normalized_start = as_utc_datetime(started_at) or started_at
    return normalized_start + timedelta(minutes=int(runtime_limit_minutes))


def emit_run_terminal_event(
    *,
    db: Session,
    run: RunDB,
    terminal_status: str,
    terminal_reason: str,
    terminal_source: str,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    context = build_run_context(run, db)
    environment_config = (
        context.get("environment_config") if isinstance(context.get("environment_config"), dict) else {}
    )
    payload: dict[str, Any] = {
        "event_type": "run_terminal",
        "run_id": run.run_id,
        "terminal_status": terminal_status,
        "terminal_reason": terminal_reason,
        "terminal_source": terminal_source,
        "started_at": _to_utc_iso(run.started_at),
        "ended_at": _to_utc_iso(run.ended_at),
        "recorded_at": _to_utc_iso(datetime.utcnow()),
    }
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)
    payload.update(
        build_experiment_baseline_fields(
            db,
            run=run,
            agent_id=None,
        )
    )

    db.add(
        EventDB(
            run_id=run.run_id,
            environment_id=str(context.get("environment_id") or environment_config.get("environment") or "unknown"),
            agent_id=None,
            event_type="run_terminal",
            action_name="run_terminal",
            outcome=terminal_status,
            payload=payload,
            trace_id=f"run-terminal:{run.run_id}:{int(datetime.utcnow().timestamp())}",
        )
    )


def _emit_run_timeout_event(
    *,
    db: Session,
    run: RunDB,
    runtime_limit_minutes: int,
    enforcement_source: str,
    deadline_at: datetime | None,
    elapsed_seconds: int | None,
) -> None:
    context = build_run_context(run, db)
    payload: dict[str, Any] = {
        "event_type": "run_timeout",
        "run_id": run.run_id,
        "runtime_limit_minutes": runtime_limit_minutes,
        "runtime_limit_seconds": int(runtime_limit_minutes) * 60,
        "deadline_at": _to_utc_iso(deadline_at),
        "elapsed_seconds": elapsed_seconds,
        "enforced_at": _to_utc_iso(datetime.utcnow()),
        "enforcement_source": enforcement_source,
    }
    payload.update(build_experiment_baseline_fields(db, run=run, agent_id=None))
    db.add(
        EventDB(
            run_id=run.run_id,
            environment_id=str(context.get("environment_id") or "unknown"),
            agent_id=None,
            event_type="run_timeout",
            action_name="runtime_limit_enforced",
            outcome="timed_out",
            payload=payload,
            trace_id=f"run-timeout:{run.run_id}:{int(datetime.utcnow().timestamp())}",
        )
    )


def _emit_run_max_ticks_event(
    *,
    db: Session,
    run: RunDB,
    max_ticks: int,
    enforcement_source: str,
    tick_count: int,
) -> None:
    context = build_run_context(run, db)
    payload: dict[str, Any] = {
        "event_type": "run_max_ticks",
        "run_id": run.run_id,
        "max_ticks": int(max_ticks),
        "tick_count": int(tick_count),
        "enforced_at": _to_utc_iso(datetime.utcnow()),
        "enforcement_source": enforcement_source,
    }
    payload.update(build_experiment_baseline_fields(db, run=run, agent_id=None))
    db.add(
        EventDB(
            run_id=run.run_id,
            environment_id=str(context.get("environment_id") or "unknown"),
            agent_id=None,
            event_type="run_max_ticks",
            action_name="max_ticks_enforced",
            outcome="completed",
            payload=payload,
            trace_id=f"run-max-ticks:{run.run_id}:{int(datetime.utcnow().timestamp())}",
        )
    )


def _emit_run_max_agent_heartbeats_event(
    *,
    db: Session,
    run: RunDB,
    max_heartbeats_per_agent: int,
    enforcement_source: str,
    satisfied_agents: int,
    target_agents: int,
) -> None:
    context = build_run_context(run, db)
    payload: dict[str, Any] = {
        "event_type": "run_max_heartbeats_per_agent",
        "run_id": run.run_id,
        "max_heartbeats_per_agent": int(max_heartbeats_per_agent),
        "satisfied_agents": int(satisfied_agents),
        "target_agents": int(target_agents),
        "enforced_at": _to_utc_iso(datetime.utcnow()),
        "enforcement_source": enforcement_source,
    }
    payload.update(build_experiment_baseline_fields(db, run=run, agent_id=None))
    db.add(
        EventDB(
            run_id=run.run_id,
            environment_id=str(context.get("environment_id") or "unknown"),
            agent_id=None,
            event_type="run_max_heartbeats_per_agent",
            action_name="max_heartbeats_per_agent_enforced",
            outcome="completed",
            payload=payload,
            trace_id=f"run-max-heartbeats:{run.run_id}:{int(datetime.utcnow().timestamp())}",
        )
    )


def _resolve_agent_launcher_url(run_id: str, db: Session | None = None) -> str:
    if db is not None:
        run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
        if run is not None:
            service_urls = resolve_run_service_urls(run, db)
            candidate = str(service_urls.get("agent_worker") or "").strip()
            if candidate:
                return candidate

    run_status = run_launcher.get_run_status(run_id)
    services = run_status.get("services", {}) if isinstance(run_status, dict) else {}
    if isinstance(services, dict):
        for service_key, service_data in services.items():
            key = str(service_key).lower()
            if "agent" not in key and "launcher" not in key:
                continue
            if isinstance(service_data, dict):
                container_name = service_data.get("name")
                if container_name:
                    return f"http://{container_name}:8000"
        for service_data in services.values():
            if not isinstance(service_data, dict):
                continue
            container_name = str(service_data.get("name") or "")
            if "agent" in container_name.lower():
                return f"http://{container_name}:8000"

    return settings.get_agent_worker_url(run_id)


async def _scheduler_status_for_run(
    run_id: str,
    db: Session | None = None,
    *,
    include_agent_states: bool = False,
) -> dict[str, Any]:
    scheduler_url = f"{_resolve_agent_launcher_url(run_id, db=db)}/scheduler/status"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                scheduler_url,
                params={"include_agent_states": "true" if include_agent_states else "false"},
            )
            if response.status_code in (200, 201):
                payload = response.json() if response.text else {}
                return payload if isinstance(payload, dict) else {}
            raise HTTPException(
                status_code=502,
                detail=f"Scheduler status failed for run {run_id}: {response.status_code} {response.text}",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to reach scheduler for run {run_id}: {str(exc)}",
            ) from exc


async def _scheduler_progress_for_run(run_id: str, db: Session | None = None) -> dict[str, Any]:
    scheduler_url = f"{_resolve_agent_launcher_url(run_id, db=db)}/scheduler/progress"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(scheduler_url)
            if response.status_code in (200, 201):
                payload = response.json() if response.text else {}
                return payload if isinstance(payload, dict) else {}
            if response.status_code == 404:
                status = await _scheduler_status_for_run(run_id, db=db, include_agent_states=True)
                agent_states = status.get("agent_states")
                normalized_states = agent_states if isinstance(agent_states, list) else []
                heartbeat_indexes = [
                    coerce_int(item.get("heartbeat_index"), 0)
                    for item in normalized_states
                    if isinstance(item, dict)
                ]
                active_agents = sum(
                    1
                    for item in normalized_states
                    if isinstance(item, dict) and str(item.get("status") or "") in {"active", "running"}
                )
                completed_agents = sum(
                    1
                    for item in normalized_states
                    if isinstance(item, dict) and str(item.get("status") or "") == "completed"
                )
                failed_agents = sum(
                    1
                    for item in normalized_states
                    if isinstance(item, dict)
                    and (
                        str(item.get("status") or "") == "failed"
                        or coerce_int(item.get("consecutive_failures"), 0) > 0
                    )
                )
                return {
                    "state": str(status.get("state") or "unknown"),
                    "tick_count": coerce_int(status.get("tick_count"), 0),
                    "start_time": status.get("start_time"),
                    "uptime_seconds": coerce_optional_float(status.get("uptime_seconds")) or 0.0,
                    "config": status.get("config") if isinstance(status.get("config"), dict) else {},
                    "agents": {
                        "total": len(normalized_states),
                        "active": active_agents,
                        "completed": completed_agents,
                        "failed": failed_agents,
                    },
                    "progress": {
                        "min_heartbeat_index": min(heartbeat_indexes) if heartbeat_indexes else 0,
                        "max_heartbeat_index": max(heartbeat_indexes) if heartbeat_indexes else 0,
                        "completed_agents": completed_agents,
                        "active_agents": active_agents,
                        "failed_agents": failed_agents,
                        "total_agents": len(normalized_states),
                    },
                }

            raise HTTPException(
                status_code=502,
                detail=f"Scheduler progress failed for run {run_id}: {response.status_code} {response.text}",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to reach scheduler for run {run_id}: {str(exc)}",
            ) from exc


def schedule_run_runtime_limit_task(
    run_id: str,
    runtime_limit_minutes: int,
    *,
    delay_seconds: float | None = None,
    enforcement_source: str = "run_start",
) -> None:
    cancel_run_runtime_limit_task(run_id)
    computed_delay_seconds = (
        max(float(delay_seconds), 0.0) if delay_seconds is not None else max(int(runtime_limit_minutes), 0) * 60
    )

    async def _enforce_runtime_limit() -> None:
        try:
            await asyncio.sleep(computed_delay_seconds)
            db = SessionLocal()
            try:
                run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
                if not run or run.status not in (RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value):
                    return

                deadline_at = _compute_run_runtime_limit_deadline(run.started_at, runtime_limit_minutes)
                elapsed_seconds = None
                if run.started_at:
                    started_at_utc = as_utc_datetime(run.started_at)
                    if started_at_utc is not None:
                        now_utc = datetime.now(timezone.utc)
                        elapsed_seconds = int(max((now_utc - started_at_utc).total_seconds(), 0.0))

                stop_result = run_launcher.stop_run(run_id, institutional_mode=bool(run.institutional_mode))
                if stop_result.get("status") != "success" and stop_result.get("status") != "not_found":
                    error_message = stop_result.get("error", "Unknown error")
                    logger.error(
                        "run_runtime_limit_stop_failed run_id=%s limit_minutes=%s error=%s",
                        run_id,
                        runtime_limit_minutes,
                        error_message,
                    )
                    run.status = RunStatusEnum.FAILED.value
                    run.ended_at = datetime.utcnow()
                    emit_run_terminal_event(
                        db=db,
                        run=run,
                        terminal_status=RunStatusEnum.FAILED.value,
                        terminal_reason=f"runtime_limit_stop_failed: {error_message}",
                        terminal_source=enforcement_source,
                        extra_payload={
                            "runtime_limit_minutes": runtime_limit_minutes,
                            "runtime_limit_seconds": int(runtime_limit_minutes) * 60,
                            "error": error_message,
                        },
                    )
                    db.commit()
                    return

                run.status = RunStatusEnum.TIMED_OUT.value
                run.ended_at = datetime.utcnow()
                _emit_run_timeout_event(
                    db=db,
                    run=run,
                    runtime_limit_minutes=runtime_limit_minutes,
                    enforcement_source=enforcement_source,
                    deadline_at=deadline_at,
                    elapsed_seconds=elapsed_seconds,
                )
                emit_run_terminal_event(
                    db=db,
                    run=run,
                    terminal_status=RunStatusEnum.TIMED_OUT.value,
                    terminal_reason="runtime_limit_enforced",
                    terminal_source=enforcement_source,
                    extra_payload={
                        "runtime_limit_minutes": runtime_limit_minutes,
                        "runtime_limit_seconds": int(runtime_limit_minutes) * 60,
                        "deadline_at": _to_utc_iso(deadline_at),
                        "elapsed_seconds": elapsed_seconds,
                    },
                )
                db.commit()
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.debug("run_runtime_limit_task_cancelled run_id=%s", run_id)
            raise
        except Exception as exc:
            logger.error("run_runtime_limit_task_error run_id=%s limit_minutes=%s error=%s", run_id, runtime_limit_minutes, exc)
        finally:
            if RUN_RUNTIME_LIMIT_TASKS.get(run_id) is asyncio.current_task():
                RUN_RUNTIME_LIMIT_TASKS.pop(run_id, None)

    RUN_RUNTIME_LIMIT_TASKS[run_id] = asyncio.create_task(
        _enforce_runtime_limit(),
        name=f"run-runtime-limit-{run_id}",
    )


def schedule_run_max_tick_task(
    run_id: str,
    max_ticks: int,
    *,
    enforcement_source: str = "run_start",
    poll_interval_seconds: float = 5.0,
) -> None:
    cancel_run_max_tick_task(run_id)

    async def _enforce_max_ticks() -> None:
        try:
            while True:
                await asyncio.sleep(max(float(poll_interval_seconds), 1.0))
                db = SessionLocal()
                try:
                    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
                    if not run or run.status not in (RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value):
                        return
                    try:
                        scheduler_status = await _scheduler_status_for_run(run_id, db=db)
                    except HTTPException as exc:
                        logger.warning(
                            "run_max_ticks_scheduler_status_failed run_id=%s max_ticks=%s detail=%s",
                            run_id,
                            max_ticks,
                            exc.detail,
                        )
                        continue

                    tick_count = coerce_int(scheduler_status.get("tick_count"), 0)
                    if tick_count < int(max_ticks):
                        continue

                    stop_result = run_launcher.stop_run(run_id, institutional_mode=bool(run.institutional_mode))
                    if stop_result.get("status") != "success" and stop_result.get("status") != "not_found":
                        error_message = stop_result.get("error", "Unknown error")
                        run.status = RunStatusEnum.FAILED.value
                        run.ended_at = datetime.utcnow()
                        emit_run_terminal_event(
                            db=db,
                            run=run,
                            terminal_status=RunStatusEnum.FAILED.value,
                            terminal_reason=f"max_ticks_stop_failed: {error_message}",
                            terminal_source=enforcement_source,
                            extra_payload={"max_ticks": int(max_ticks), "tick_count": tick_count, "error": error_message},
                        )
                        db.commit()
                        return

                    run.status = RunStatusEnum.COMPLETED.value
                    run.ended_at = datetime.utcnow()
                    _emit_run_max_ticks_event(
                        db=db,
                        run=run,
                        max_ticks=max_ticks,
                        enforcement_source=enforcement_source,
                        tick_count=tick_count,
                    )
                    emit_run_terminal_event(
                        db=db,
                        run=run,
                        terminal_status=RunStatusEnum.COMPLETED.value,
                        terminal_reason="max_ticks_enforced",
                        terminal_source=enforcement_source,
                        extra_payload={"max_ticks": int(max_ticks), "tick_count": tick_count},
                    )
                    db.commit()
                    return
                finally:
                    db.close()
        except asyncio.CancelledError:
            logger.debug("run_max_tick_task_cancelled run_id=%s", run_id)
            raise
        except Exception as exc:
            logger.error("run_max_tick_task_error run_id=%s max_ticks=%s error=%s", run_id, max_ticks, exc)
        finally:
            if RUN_MAX_TICK_TASKS.get(run_id) is asyncio.current_task():
                RUN_MAX_TICK_TASKS.pop(run_id, None)

    RUN_MAX_TICK_TASKS[run_id] = asyncio.create_task(
        _enforce_max_ticks(),
        name=f"run-max-ticks-{run_id}",
    )


def schedule_run_max_agent_heartbeat_task(
    run_id: str,
    max_heartbeats_per_agent: int,
    *,
    enforcement_source: str = "run_start",
    poll_interval_seconds: float = 5.0,
    target_agents: int | None = None,
) -> None:
    cancel_run_max_agent_heartbeat_task(run_id)

    async def _enforce_max_heartbeats() -> None:
        cached_target_agents = int(target_agents) if target_agents is not None and int(target_agents) > 0 else None
        try:
            while True:
                effective_poll_interval = max(float(poll_interval_seconds), 1.0)
                if cached_target_agents and cached_target_agents >= 1000:
                    effective_poll_interval = max(effective_poll_interval, 20.0)
                elif cached_target_agents and cached_target_agents >= 250:
                    effective_poll_interval = max(effective_poll_interval, 10.0)
                await asyncio.sleep(effective_poll_interval)
                db = SessionLocal()
                try:
                    run = db.query(RunDB).filter(RunDB.run_id == run_id).first()
                    if not run or run.status not in (RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value):
                        return
                    try:
                        scheduler_progress = await _scheduler_progress_for_run(run_id, db=db)
                    except HTTPException as exc:
                        logger.warning(
                            "run_max_agent_heartbeats_scheduler_progress_failed run_id=%s cap=%s detail=%s",
                            run_id,
                            max_heartbeats_per_agent,
                            exc.detail,
                        )
                        continue

                    progress = scheduler_progress.get("progress")
                    if not isinstance(progress, dict) or not progress:
                        continue

                    if cached_target_agents is None:
                        cached_target_agents = (
                            db.query(RunAgentAssignmentDB)
                            .filter(RunAgentAssignmentDB.run_id == run_id)
                            .count()
                        ) or coerce_int(progress.get("total_agents"), 0)

                    satisfied_agents = coerce_int(progress.get("completed_agents"), 0)
                    if satisfied_agents < int(cached_target_agents or 0):
                        continue

                    stop_result = run_launcher.stop_run(run_id, institutional_mode=bool(run.institutional_mode))
                    if stop_result.get("status") != "success" and stop_result.get("status") != "not_found":
                        error_message = stop_result.get("error", "Unknown error")
                        run.status = RunStatusEnum.FAILED.value
                        run.ended_at = datetime.utcnow()
                        emit_run_terminal_event(
                            db=db,
                            run=run,
                            terminal_status=RunStatusEnum.FAILED.value,
                            terminal_reason=f"max_heartbeats_per_agent_stop_failed: {error_message}",
                            terminal_source=enforcement_source,
                            extra_payload={
                                "max_heartbeats_per_agent": int(max_heartbeats_per_agent),
                                "satisfied_agents": satisfied_agents,
                                "target_agents": cached_target_agents,
                                "error": error_message,
                            },
                        )
                        db.commit()
                        return

                    run.status = RunStatusEnum.COMPLETED.value
                    run.ended_at = datetime.utcnow()
                    _emit_run_max_agent_heartbeats_event(
                        db=db,
                        run=run,
                        max_heartbeats_per_agent=max_heartbeats_per_agent,
                        enforcement_source=enforcement_source,
                        satisfied_agents=satisfied_agents,
                        target_agents=cached_target_agents or 0,
                    )
                    emit_run_terminal_event(
                        db=db,
                        run=run,
                        terminal_status=RunStatusEnum.COMPLETED.value,
                        terminal_reason="max_heartbeats_per_agent_enforced",
                        terminal_source=enforcement_source,
                        extra_payload={
                            "max_heartbeats_per_agent": int(max_heartbeats_per_agent),
                            "satisfied_agents": satisfied_agents,
                            "target_agents": cached_target_agents,
                        },
                    )
                    db.commit()
                    return
                finally:
                    db.close()
        except asyncio.CancelledError:
            logger.debug("run_max_agent_heartbeat_task_cancelled run_id=%s", run_id)
            raise
        except Exception as exc:
            logger.error(
                "run_max_agent_heartbeat_task_error run_id=%s cap=%s error=%s",
                run_id,
                max_heartbeats_per_agent,
                exc,
            )
        finally:
            if RUN_MAX_AGENT_HEARTBEAT_TASKS.get(run_id) is asyncio.current_task():
                RUN_MAX_AGENT_HEARTBEAT_TASKS.pop(run_id, None)

    RUN_MAX_AGENT_HEARTBEAT_TASKS[run_id] = asyncio.create_task(
        _enforce_max_heartbeats(),
        name=f"run-max-heartbeats-per-agent-{run_id}",
    )


def recover_run_runtime_limit_tasks_on_startup() -> dict[str, int]:
    db = SessionLocal()
    scheduled = 0
    overdue = 0
    skipped_no_limit = 0
    skipped_terminal = 0
    patched_missing_start = 0
    try:
        active_runs = (
            db.query(RunDB)
            .filter(RunDB.status.in_([RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value]))
            .all()
        )
        now_utc = datetime.utcnow()
        for run in active_runs:
            context = build_run_context(run, db)
            runtime_limit_minutes, _ = resolve_run_runtime_limit(context.get("environment_config"))
            if runtime_limit_minutes is None:
                skipped_no_limit += 1
                cancel_run_runtime_limit_task(run.run_id)
                continue
            if run.status in TERMINAL_RUN_STATUSES:
                skipped_terminal += 1
                cancel_run_runtime_limit_task(run.run_id)
                continue
            if run.started_at is None:
                run.started_at = now_utc
                patched_missing_start += 1
            deadline_at = _compute_run_runtime_limit_deadline(run.started_at, runtime_limit_minutes)
            if deadline_at is None:
                skipped_no_limit += 1
                continue
            remaining_seconds = (deadline_at - (as_utc_datetime(now_utc) or now_utc)).total_seconds()
            if remaining_seconds <= 0:
                overdue += 1
                schedule_run_runtime_limit_task(
                    run_id=run.run_id,
                    runtime_limit_minutes=runtime_limit_minutes,
                    delay_seconds=0.0,
                    enforcement_source="startup_recovery_overdue",
                )
            else:
                scheduled += 1
                schedule_run_runtime_limit_task(
                    run_id=run.run_id,
                    runtime_limit_minutes=runtime_limit_minutes,
                    delay_seconds=remaining_seconds,
                    enforcement_source="startup_recovery",
                )
        if patched_missing_start > 0:
            db.commit()
        return {
            "scheduled": scheduled,
            "overdue": overdue,
            "skipped_no_limit": skipped_no_limit,
            "skipped_terminal": skipped_terminal,
            "patched_missing_start": patched_missing_start,
        }
    finally:
        db.close()


def recover_run_max_tick_tasks_on_startup() -> dict[str, int]:
    db = SessionLocal()
    scheduled = 0
    skipped_no_limit = 0
    skipped_terminal = 0
    try:
        active_runs = (
            db.query(RunDB)
            .filter(RunDB.status.in_([RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value]))
            .all()
        )
        for run in active_runs:
            context = build_run_context(run, db)
            max_ticks, _ = resolve_run_max_ticks(context.get("environment_config"))
            if max_ticks is None:
                skipped_no_limit += 1
                cancel_run_max_tick_task(run.run_id)
                continue
            if run.status in TERMINAL_RUN_STATUSES:
                skipped_terminal += 1
                cancel_run_max_tick_task(run.run_id)
                continue
            scheduled += 1
            schedule_run_max_tick_task(
                run_id=run.run_id,
                max_ticks=max_ticks,
                enforcement_source="startup_recovery",
            )
        return {
            "scheduled": scheduled,
            "skipped_no_limit": skipped_no_limit,
            "skipped_terminal": skipped_terminal,
        }
    finally:
        db.close()


def recover_run_max_agent_heartbeat_tasks_on_startup() -> dict[str, int]:
    db = SessionLocal()
    scheduled = 0
    skipped_no_limit = 0
    skipped_terminal = 0
    try:
        active_runs = (
            db.query(RunDB)
            .filter(RunDB.status.in_([RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value]))
            .all()
        )
        for run in active_runs:
            context = build_run_context(run, db)
            max_heartbeats_per_agent, _ = resolve_run_max_heartbeats_per_agent(context.get("environment_config"))
            if max_heartbeats_per_agent is None:
                skipped_no_limit += 1
                cancel_run_max_agent_heartbeat_task(run.run_id)
                continue
            if run.status in TERMINAL_RUN_STATUSES:
                skipped_terminal += 1
                cancel_run_max_agent_heartbeat_task(run.run_id)
                continue
            scheduled += 1
            target_agents = (
                db.query(RunAgentAssignmentDB)
                .filter(RunAgentAssignmentDB.run_id == run.run_id)
                .count()
            ) or 0
            schedule_run_max_agent_heartbeat_task(
                run_id=run.run_id,
                max_heartbeats_per_agent=max_heartbeats_per_agent,
                enforcement_source="startup_recovery",
                target_agents=target_agents,
            )
        return {
            "scheduled": scheduled,
            "skipped_no_limit": skipped_no_limit,
            "skipped_terminal": skipped_terminal,
        }
    finally:
        db.close()
