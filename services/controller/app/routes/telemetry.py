"""Telemetry ingestion and query routes for run-scoped events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import run_binding
from app.config import settings
from app.database import (
    AgentActionEvent,
    AgentMetrics,
    Event as EventDB,
    Run,
    RunEnvironmentAssignment,
    RunMetrics,
    RunStatus as RunStatusEnum,
    get_db,
)
from app.redis_client import get_agent_channel, get_run_channel, redis_client
from app.run_launcher import run_launcher
from app.telemetry_baseline import build_experiment_baseline_fields


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


class TelemetryEvent(BaseModel):
    """Single telemetry event for batch ingestion."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique event ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")
    run_id: str = Field(..., description="Parent run identifier")
    agent_id: Optional[str] = Field(default=None, description="Agent identifier")
    source: str = Field(..., pattern="^(agent|system)$", description="Event source")
    action_category: str = Field(
        ...,
        pattern="^(Self|Environmental|System)$",
        description="Action category",
    )
    action_type: str = Field(..., description="Action type")
    skill_name: Optional[str] = Field(default=None, description="Skill name if applicable")
    intent: Optional[str] = Field(default=None, description="Intent description")
    parent_event_id: Optional[str] = Field(default=None, description="Parent event for tracing")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event payload data")
    success: bool = Field(default=True, description="Whether action succeeded")
    duration_ms: Optional[int] = Field(default=None, description="Action duration in milliseconds")
    error_message: Optional[str] = Field(default=None, description="Error message if failed")
    llm_tokens_input: int = Field(default=0, description="Input tokens consumed")
    llm_tokens_output: int = Field(default=0, description="Output tokens generated")
    llm_cost_usd: float = Field(default=0.0, description="LLM cost in USD")
    ia_cost_usd: float = Field(default=0.0, description="Auxiliary service cost in USD")
    ia_metadata: Dict[str, Any] = Field(default_factory=dict, description="Auxiliary service metadata")

    model_config = {
        "json_schema_extra": {
            "example": {
                "event_id": "evt_123",
                "timestamp": "2026-02-10T12:00:00Z",
                "run_id": "run_xyz",
                "agent_id": "agent_001",
                "source": "agent",
                "action_category": "Environmental",
                "action_type": "http_post",
                "skill_name": "messaging",
                "intent": "Post message to feed",
                "payload": {"endpoint": "/feed", "content": "Hello"},
                "success": True,
                "duration_ms": 150,
                "llm_tokens_input": 100,
                "llm_tokens_output": 50,
                "llm_cost_usd": 0.0025,
            }
        }
    }


class BatchEventsRequest(BaseModel):
    """Request model for batch event ingestion."""

    events: List[TelemetryEvent] = Field(..., min_length=1, max_length=1000)

    @field_validator("events")
    @classmethod
    def validate_events_not_empty(cls, value: List[TelemetryEvent]) -> List[TelemetryEvent]:
        if not value:
            raise ValueError("Events list cannot be empty")
        return value


class BatchEventsResponse(BaseModel):
    """Response model for batch event ingestion."""

    status: str
    accepted_count: int
    rejected_count: int
    event_ids: List[str]
    errors: Optional[List[Dict[str, str]]] = None


class TelemetryQueryParams(BaseModel):
    """Query parameters for telemetry filtering."""

    agent_id: Optional[str] = None
    source: Optional[str] = None
    action_category: Optional[str] = None
    action_type: Optional[str] = None
    skill_name: Optional[str] = None
    success: Optional[bool] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: int = Field(default=100, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)


def _is_agent_action_attempt(event: TelemetryEvent) -> bool:
    if event.source != "agent" or not event.agent_id:
        return False
    if event.action_type in {"heartbeat", "heartbeat_ok", "llm_call"}:
        return False
    if event.action_type.startswith("http_") or event.action_type.startswith("fs_"):
        return True
    if event.payload.get("event_type") == "action_attempt":
        return True
    return event.action_category in {"Environmental", "Self"}


def _coerce_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_cost_limits(environment_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(environment_config, dict):
        return {}
    limits = environment_config.get("cost_limits") or environment_config.get("budget") or {}
    if not isinstance(limits, dict):
        return {}
    max_total = _coerce_optional_float(
        limits.get("max_total_cost_usd")
        if "max_total_cost_usd" in limits
        else limits.get("max_cost_usd")
    )
    max_per_agent = _coerce_optional_float(
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


def _resolve_environment_id_for_run(run: Run, db: Session) -> str:
    context = run_binding.build_run_context(run, db)
    environment_id = str(context.get("environment_id") or "").strip()
    if environment_id:
        return environment_id
    environment_ref = str(context.get("environment_ref") or "").strip()
    normalized = run_binding.normalize_environment_ref(environment_ref)
    if normalized:
        return normalized
    assignment = (
        db.query(RunEnvironmentAssignment)
        .filter(RunEnvironmentAssignment.run_id == run.run_id)
        .first()
    )
    if assignment and assignment.environment_id:
        return str(assignment.environment_id)
    return settings.get_default_environment_id()


def _resolve_agent_launcher_url(run_id: str, db: Optional[Session] = None) -> str:
    fallback_environment: Optional[str] = None
    if db is not None:
        run = db.query(Run).filter(Run.run_id == run_id).first()
        if run is not None:
            environment_id = _resolve_environment_id_for_run(run, db)
            fallback_environment = environment_id
            try:
                service_urls = run_launcher.get_service_urls(
                    run_id=run.run_id,
                    environment_id=environment_id,
                )
                candidate = str(service_urls.get("agent_worker") or "").strip()
                if candidate:
                    return candidate
            except Exception as exc:
                logger.warning(
                    "telemetry_agent_url_descriptor_fallback run_id=%s environment=%s error=%s",
                    run_id,
                    environment_id,
                    exc,
                )

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
    return settings.get_agent_worker_url(run_id, environment_id=fallback_environment)


async def _scheduler_pause_for_run(run_id: str, db: Optional[Session] = None) -> bool:
    scheduler_url = f"{_resolve_agent_launcher_url(run_id, db=db)}/scheduler/pause"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(scheduler_url)

    if response.status_code in (200, 201):
        return True
    if response.status_code == 400:
        payload = response.json() if response.text else {}
        detail = str(payload.get("detail") or "").lower()
        if "state: paused" in detail:
            return True
    logger.warning(
        "Scheduler pause failed for run %s: %s %s",
        run_id,
        response.status_code,
        response.text,
    )
    return False


def _sum_run_costs(db: Session, run_id: str) -> Tuple[float, float, float]:
    totals = (
        db.query(
            func.coalesce(func.sum(AgentActionEvent.llm_cost_usd), 0.0).label("llm_cost_usd"),
            func.coalesce(func.sum(AgentActionEvent.ia_cost_usd), 0.0).label("ia_cost_usd"),
        )
        .filter(AgentActionEvent.run_id == run_id)
        .one()
    )
    llm_cost = float(totals.llm_cost_usd or 0.0)
    ia_cost = float(totals.ia_cost_usd or 0.0)
    return llm_cost, ia_cost, llm_cost + ia_cost


def _max_agent_cost(db: Session, run_id: str) -> Tuple[Optional[str], float]:
    rows = (
        db.query(
            AgentActionEvent.agent_id.label("agent_id"),
            func.coalesce(func.sum(AgentActionEvent.llm_cost_usd), 0.0).label("llm_cost_usd"),
            func.coalesce(func.sum(AgentActionEvent.ia_cost_usd), 0.0).label("ia_cost_usd"),
        )
        .filter(AgentActionEvent.run_id == run_id)
        .filter(AgentActionEvent.agent_id.isnot(None))
        .group_by(AgentActionEvent.agent_id)
        .all()
    )
    winner_id: Optional[str] = None
    winner_cost = 0.0
    for row in rows:
        total = float(row.llm_cost_usd or 0.0) + float(row.ia_cost_usd or 0.0)
        if total > winner_cost:
            winner_cost = total
            winner_id = row.agent_id
    return winner_id, winner_cost


async def _enforce_cost_limits_for_runs(db: Session, run_ids: Set[str]) -> None:
    for run_id in sorted(run_ids):
        run = db.query(Run).filter(Run.run_id == run_id).first()
        if not run:
            continue
        run_context = run_binding.build_run_context(run, db)
        environment_config = run_context.get("environment_config")
        cost_limits = _extract_cost_limits(environment_config)
        if not cost_limits:
            continue
        max_total = cost_limits.get("max_total_cost_usd")
        max_per_agent = cost_limits.get("max_cost_usd_per_agent")
        if max_total is None and max_per_agent is None:
            continue

        llm_cost, ia_cost, total_cost = _sum_run_costs(db, run_id)
        offender_agent_id = None
        offender_agent_cost = 0.0
        if max_per_agent is not None:
            offender_agent_id, offender_agent_cost = _max_agent_cost(db, run_id)

        breached = False
        reasons: List[str] = []
        if max_total is not None and total_cost >= float(max_total):
            breached = True
            reasons.append("max_total_cost_usd")
        if max_per_agent is not None and offender_agent_cost >= float(max_per_agent):
            breached = True
            reasons.append("max_cost_usd_per_agent")
        if not breached:
            continue
        if run.status not in (RunStatusEnum.RUNNING.value, RunStatusEnum.PAUSED.value):
            continue

        action_taken = None
        on_breach = str(cost_limits.get("on_breach") or "pause").lower()
        if on_breach == "stop":
            try:
                await asyncio.to_thread(
                    run_launcher.stop_run,
                    run_id,
                )
            except Exception as exc:
                logger.warning("Budget stop failed for run %s: %s", run_id, exc)
            run.status = RunStatusEnum.CANCELLED.value
            run.ended_at = datetime.utcnow()
            action_taken = "stop"
        else:
            paused = await _scheduler_pause_for_run(run_id, db=db)
            if paused:
                run.status = RunStatusEnum.PAUSED.value
                action_taken = "pause"

        breach_payload = {
            "event_type": "budget_breach",
            "run_id": run_id,
            "reasons": reasons,
            "on_breach": on_breach,
            "action_taken": action_taken,
            "cost_limits": cost_limits,
            "totals": {
                "llm_cost_usd": llm_cost,
                "ia_cost_usd": ia_cost,
                "total_cost_usd": total_cost,
            },
            "offender": {
                "agent_id": offender_agent_id,
                "agent_cost_usd": offender_agent_cost,
            },
        }
        breach_payload.update(
            build_experiment_baseline_fields(
                db,
                run=run,
                agent_id=None,
            )
        )
        environment_id = str(run_context.get("environment_id") or "unknown")
        db.add(
            EventDB(
                event_id=str(uuid4()),
                run_id=run_id,
                environment_id=environment_id,
                agent_id=None,
                event_type="budget_breach",
                action_name="budget_breach",
                outcome="blocked",
                payload=breach_payload,
                trace_id=None,
                timestamp=datetime.utcnow(),
            )
        )
        db.commit()


@router.post("/events:batch", response_model=BatchEventsResponse)
async def ingest_batch_events(
    request: BatchEventsRequest,
    db: Session = Depends(get_db),
) -> BatchEventsResponse:
    return await persist_batch_events(request, db=db)


async def persist_batch_events(
    request: BatchEventsRequest,
    *,
    db: Session,
) -> BatchEventsResponse:
    accepted_ids: List[str] = []
    rejected_errors: List[Dict[str, str]] = []
    db_events: List[AgentActionEvent] = []
    legacy_rows: List[EventDB] = []
    run_cache: Dict[str, Run] = {}
    assignment_cache_by_run: Dict[str, Dict[str, Optional[str]]] = {}

    for idx, event in enumerate(request.events):
        try:
            run = run_cache.get(event.run_id)
            if run is None:
                run = db.query(Run).filter(Run.run_id == event.run_id).first()
                if run:
                    run_cache[event.run_id] = run
            if not run:
                rejected_errors.append(
                    {
                        "index": str(idx),
                        "event_id": event.event_id,
                        "error": f"Run {event.run_id} not found",
                    }
                )
                continue

            assignment_cache = assignment_cache_by_run.setdefault(event.run_id, {})
            baseline_fields = build_experiment_baseline_fields(
                db,
                run=run,
                agent_id=event.agent_id,
                assignment_cache=assignment_cache,
            )
            event_payload = dict(event.payload or {})
            event_payload.update(baseline_fields)

            db_events.append(
                AgentActionEvent(
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    run_id=event.run_id,
                    agent_id=event.agent_id,
                    source=event.source,
                    action_category=event.action_category,
                    action_type=event.action_type,
                    skill_name=event.skill_name,
                    intent=event.intent,
                    parent_event_id=event.parent_event_id,
                    payload=event_payload,
                    success=event.success,
                    duration_ms=event.duration_ms,
                    error_message=event.error_message,
                    llm_tokens_input=event.llm_tokens_input,
                    llm_tokens_output=event.llm_tokens_output,
                    llm_cost_usd=event.llm_cost_usd,
                    ia_cost_usd=event.ia_cost_usd,
                    ia_metadata=event.ia_metadata,
                )
            )
            accepted_ids.append(event.event_id)

            run_context = run_binding.build_run_context(run, db)
            environment_id = (
                event.payload.get("environment_name")
                or str(run_context.get("environment_id") or "")
                or str(run_context.get("environment_ref") or "")
                or "unknown"
            )
            legacy_rows.append(
                EventDB(
                    event_id=str(uuid4()),
                    run_id=event.run_id,
                    environment_id=str(environment_id),
                    agent_id=event.agent_id,
                    event_type=str(event.payload.get("event_type") or event.action_type or "telemetry")[:50],
                    action_name=str(event.payload.get("action_name") or event.action_type or "unknown")[:100],
                    outcome="success" if event.success else "error",
                    payload={
                        "source": event.source,
                        "action_category": event.action_category,
                        "action_type": event.action_type,
                        "action_name": event.payload.get("action_name"),
                        "method": event.payload.get("method"),
                        "path": event.payload.get("path"),
                        "status_code": event.payload.get("status_code"),
                        "request_id": event.payload.get("request_id"),
                        "duration_ms": event.duration_ms,
                        "payload": event.payload,
                        "error_message": event.error_message,
                        **baseline_fields,
                    },
                    trace_id=event.parent_event_id,
                    timestamp=event.timestamp,
                )
            )
        except Exception as exc:
            rejected_errors.append(
                {
                    "index": str(idx),
                    "event_id": getattr(event, "event_id", "unknown"),
                    "error": str(exc),
                }
            )

    try:
        if db_events or legacy_rows:
            db.add_all(db_events)
            db.add_all(legacy_rows)
            db.commit()
            logger.info(
                "Persisted telemetry batch (agent_action_events=%s, events=%s)",
                len(db_events),
                len(legacy_rows),
            )
    except Exception as exc:
        db.rollback()
        logger.error("Database error during batch ingest: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist events: {exc}",
        )

    try:
        accepted_set = set(accepted_ids)
        run_ids_with_cost = {
            event.run_id
            for event in request.events
            if event.event_id in accepted_set and (event.llm_cost_usd > 0.0 or event.ia_cost_usd > 0.0)
        }
        if run_ids_with_cost:
            await _enforce_cost_limits_for_runs(db, run_ids_with_cost)
    except Exception as exc:
        logger.warning("Cost limit enforcement skipped due to error: %s", exc)

    redis_failures = 0
    for event in request.events:
        if event.event_id not in accepted_ids:
            continue
        try:
            await redis_client.publish(get_run_channel(event.run_id), event.model_dump_json())
            if event.agent_id:
                await redis_client.publish(get_agent_channel(event.agent_id), event.model_dump_json())
        except Exception as exc:
            logger.warning("Redis publish failed for event %s: %s", event.event_id, exc)
            redis_failures += 1
    if redis_failures > 0:
        logger.warning("Failed to publish %s events to Redis", redis_failures)

    return BatchEventsResponse(
        status="success" if not rejected_errors else "partial",
        accepted_count=len(accepted_ids),
        rejected_count=len(rejected_errors),
        event_ids=accepted_ids,
        errors=rejected_errors if rejected_errors else None,
    )


@router.get("/events/{run_id}")
async def query_telemetry(
    run_id: str,
    params: TelemetryQueryParams = Depends(),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    query = db.query(AgentActionEvent).filter(AgentActionEvent.run_id == run_id)
    if params.agent_id:
        query = query.filter(AgentActionEvent.agent_id == params.agent_id)
    if params.source:
        query = query.filter(AgentActionEvent.source == params.source)
    if params.action_category:
        query = query.filter(AgentActionEvent.action_category == params.action_category)
    if params.action_type:
        query = query.filter(AgentActionEvent.action_type == params.action_type)
    if params.skill_name:
        query = query.filter(AgentActionEvent.skill_name == params.skill_name)
    if params.success is not None:
        query = query.filter(AgentActionEvent.success == params.success)
    if params.since:
        query = query.filter(AgentActionEvent.timestamp >= params.since)
    if params.until:
        query = query.filter(AgentActionEvent.timestamp <= params.until)

    total = query.count()
    events = (
        query.order_by(AgentActionEvent.timestamp.desc())
        .offset(params.offset)
        .limit(params.limit)
        .all()
    )
    return {
        "run_id": run_id,
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
        "events": [
            {
                "event_id": row.event_id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "run_id": row.run_id,
                "agent_id": row.agent_id,
                "source": row.source,
                "action_category": row.action_category,
                "action_type": row.action_type,
                "skill_name": row.skill_name,
                "intent": row.intent,
                "parent_event_id": row.parent_event_id,
                "payload": row.payload,
                "success": row.success,
                "duration_ms": row.duration_ms,
                "error_message": row.error_message,
                "llm_tokens_input": row.llm_tokens_input,
                "llm_tokens_output": row.llm_tokens_output,
                "llm_cost_usd": row.llm_cost_usd,
                "ia_cost_usd": row.ia_cost_usd,
            }
            for row in events
        ],
    }


@router.get("/metrics/{run_id}")
async def get_run_metrics(
    run_id: str,
    agent_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if agent_id:
        metrics = (
            db.query(AgentMetrics)
            .filter(AgentMetrics.run_id == run_id, AgentMetrics.agent_id == agent_id)
            .order_by(AgentMetrics.window_start.desc())
            .all()
        )
        return {
            "run_id": run_id,
            "agent_id": agent_id,
            "windows": [
                {
                    "window_start": row.window_start.isoformat(),
                    "window_end": row.window_end.isoformat(),
                    "total_actions": row.total_actions,
                    "successful_actions": row.successful_actions,
                    "failed_actions": row.failed_actions,
                    "llm_cost_usd": row.llm_cost_usd,
                    "ia_cost_usd": row.ia_cost_usd,
                }
                for row in metrics
            ],
        }

    metrics = (
        db.query(RunMetrics)
        .filter(RunMetrics.run_id == run_id)
        .order_by(RunMetrics.window_start.desc())
        .all()
    )
    return {
        "run_id": run_id,
        "windows": [
            {
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
                "total_agents": row.total_agents,
                "active_agents": row.active_agents,
                "total_events": row.total_events,
                "total_cost_usd": row.total_cost_usd,
                "success_rate": row.success_rate,
            }
            for row in metrics
        ],
    }
