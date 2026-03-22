"""Event API routes for run telemetry.

Provides run-scoped event queries for the active public surface.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import Event as EventDB, Run, get_db
from app.event_pipeline import RunEvent
from app.telemetry_baseline import build_run_baseline_fields

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _as_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _merge_event_baseline_fields(
    *,
    payload: Optional[Dict[str, Any]],
    baseline_fields: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(baseline_fields or {})
    raw_payload = payload if isinstance(payload, dict) else {}
    for key in (
        "population_group",
        "role",
        "runtime_id",
        "model_id",
    ):
        if merged.get(key) is None:
            merged[key] = raw_payload.get(key)
    return merged


def _event_response(
    event: EventDB,
    *,
    baseline_fields: Optional[Dict[str, Any]] = None,
) -> "EventResponse":
    merged_baseline = _merge_event_baseline_fields(
        payload=event.payload,
        baseline_fields=baseline_fields,
    )
    return EventResponse(
        event_id=event.event_id,
        timestamp=_as_utc_datetime(event.timestamp),
        run_id=event.run_id,
        environment_id=event.environment_id,
        agent_id=event.agent_id,
        event_type=event.event_type,
        action_name=event.action_name,
        outcome=event.outcome,
        payload=event.payload,
        population_group=merged_baseline.get("population_group"),
        role=merged_baseline.get("role"),
        runtime_id=merged_baseline.get("runtime_id"),
        model_id=merged_baseline.get("model_id"),
        trace_id=event.trace_id,
    )


# ============================================================================
# Pydantic Models
# ============================================================================


class EventResponse(BaseModel):
    """Event response model."""

    event_id: str = Field(..., description="Unique event identifier")
    timestamp: datetime = Field(..., description="Event timestamp")
    run_id: str = Field(..., description="Parent run identifier")
    environment_id: str = Field(..., description="Environment identifier")
    agent_id: Optional[str] = Field(None, description="Agent identifier (null for system)")
    event_type: str = Field(..., description="Event category")
    action_name: str = Field(..., description="Action performed")
    outcome: str = Field(..., description="Action outcome")
    payload: Optional[dict] = Field(None, description="Event data")
    population_group: Optional[str] = Field(None, description="Agent population group label when available.")
    role: Optional[str] = Field(None, description="Agent role label when available.")
    runtime_id: Optional[str] = Field(None, description="Resolved runtime id for this event context.")
    model_id: Optional[str] = Field(None, description="Resolved model id for this event context.")
    trace_id: Optional[str] = Field(None, description="Distributed trace ID")

    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    """List of events with pagination info."""

    events: List[EventResponse]
    total: int = Field(..., description="Total number of events matching query")
    run_id: str
    limit: int
    offset: int


class EventCountResponse(BaseModel):
    """Event count response."""

    run_id: str
    count: int
    event_types: Optional[dict] = Field(None, description="Breakdown by event type")


class EventExportRequest(BaseModel):
    """Request to export events."""

    run_ids: List[str] = Field(..., description="List of run IDs to export")
    event_types: Optional[List[str]] = Field(None, description="Filter by event types")
    agent_ids: Optional[List[str]] = Field(None, description="Filter by agents")
    since: Optional[datetime] = Field(None, description="Start timestamp")
    until: Optional[datetime] = Field(None, description="End timestamp")


class EventExportResponse(BaseModel):
    """Exported events."""

    runs: dict = Field(..., description="Events grouped by run_id")
    total_events: int
    exported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/runs/{run_id}", response_model=EventListResponse)
async def get_run_events(
    run_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    agent_id: Optional[str] = Query(None, description="Filter by agent"),
    limit: int = Query(100, ge=1, le=10000, description="Maximum events to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
):
    """Get events for a specific run.

    Args:
        run_id: Run identifier
        event_type: Optional filter by event type
        agent_id: Optional filter by agent
        limit: Maximum events to return (default: 100, max: 10000)
        offset: Offset for pagination
        db: Database session

    Returns:
        EventListResponse with events and pagination info
    """
    # Verify run exists
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Build query
    query = db.query(EventDB).filter(EventDB.run_id == run_id)

    if event_type:
        query = query.filter(EventDB.event_type == event_type)

    if agent_id:
        query = query.filter(EventDB.agent_id == agent_id)

    # Get total count
    total = query.count()

    # Apply ordering and pagination
    events = query.order_by(EventDB.timestamp.desc()).offset(offset).limit(limit).all()
    assignment_cache: Dict[str, Dict[str, Optional[str]]] = {}

    return EventListResponse(
        events=[
            _event_response(
                event,
                baseline_fields=build_run_baseline_fields(
                    db,
                    run=run,
                    agent_id=event.agent_id,
                    assignment_cache=assignment_cache,
                ),
            )
            for event in events
        ],
        total=total,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/count", response_model=EventCountResponse)
async def get_run_event_count(run_id: str, db: Session = Depends(get_db)):
    """Get total event count for a run.

    Args:
        run_id: Run identifier
        db: Database session

    Returns:
        EventCountResponse with count and breakdown
    """
    # Verify run exists
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Get total count
    total_count = db.query(EventDB).filter(EventDB.run_id == run_id).count()

    # Get breakdown by event type
    type_counts = {}
    type_query = (
        db.query(EventDB.event_type, func.count(EventDB.event_id))
        .filter(EventDB.run_id == run_id)
        .group_by(EventDB.event_type)
        .all()
    )
    for event_type, count in type_query:
        type_counts[event_type] = count

    return EventCountResponse(
        run_id=run_id, count=total_count, event_types=type_counts if type_counts else None
    )


@router.post("/export", response_model=EventExportResponse)
async def export_events(request: EventExportRequest, db: Session = Depends(get_db)):
    """Export events for multiple runs.

    Args:
        request: Export request with run_ids and filters
        db: Database session

    Returns:
        EventExportResponse with events grouped by run
    """
    result = {}
    total_events = 0

    for run_id in request.run_ids:
        run = db.query(Run).filter(Run.run_id == run_id).first()
        if not run:
            result[run_id] = []
            continue

        # Build query
        query = db.query(EventDB).filter(EventDB.run_id == run_id)

        if request.event_types:
            query = query.filter(EventDB.event_type.in_(request.event_types))

        if request.agent_ids:
            query = query.filter(EventDB.agent_id.in_(request.agent_ids))

        if request.since:
            query = query.filter(EventDB.timestamp >= request.since)

        if request.until:
            query = query.filter(EventDB.timestamp <= request.until)

        # Get events
        events = query.order_by(EventDB.timestamp.asc()).all()
        assignment_cache: Dict[str, Optional[str]] = {}
        result[run_id] = [
            _event_response(
                event,
                baseline_fields=build_run_baseline_fields(
                    db,
                    run=run,
                    agent_id=event.agent_id,
                    assignment_cache=assignment_cache,
                ),
            ).model_dump(mode="json")
            for event in events
        ]
        total_events += len(events)

    return EventExportResponse(runs=result, total_events=total_events)


@router.post("/runs/{run_id}/ingest")
async def ingest_event(run_id: str, event: RunEvent, db: Session = Depends(get_db)):
    """Ingest a single event (for internal use).

    Args:
        run_id: Run identifier
        event: Event to ingest
        db: Database session

    Returns:
        Success confirmation
    """
    # Verify run exists
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    baseline_fields = build_run_baseline_fields(
        db,
        run=run,
        agent_id=event.agent_id,
    )
    enriched_payload = dict(event.payload or {})
    enriched_payload.update(baseline_fields)

    # Create database event
    db_event = EventDB(
        event_id=event.event_id,
        run_id=run_id,
        environment_id=event.environment_id,
        agent_id=event.agent_id,
        event_type=event.event_type,
        action_name=event.action_name,
        outcome=event.outcome,
        payload=enriched_payload,
        trace_id=event.trace_id,
        timestamp=event.timestamp,
    )

    db.add(db_event)
    db.commit()

    return {"status": "success", "event_id": event.event_id}
