"""System monitoring and diagnostics routes.

Provides visibility into MASE platform health, container status,
and event pipeline for debugging operational issues.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy.orm import Session

from app.database import Run, Event, get_db
from app.run_launcher import run_launcher
from app.config import settings
from app import run_binding

router = APIRouter(prefix="/api/v1/monitor", tags=["monitoring"])


def _probe_orchestrator_health() -> Dict[str, Any]:
    """Best-effort orchestrator health probe (Docker daemon lives there)."""
    health_url = f"{settings.orchestrator_url}/health"
    try:
        response = httpx.get(health_url, timeout=5.0)
        return {
            "reachable": response.status_code < 500,
            "status_code": response.status_code,
            "url": health_url,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "status_code": None,
            "url": health_url,
            "error": str(exc),
        }


def _extract_orchestrator_run_containers(run_id: str, status_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize orchestrator run status payload into container-like records."""
    services = status_payload.get("services")
    if not isinstance(services, dict):
        return []

    rows: List[Dict[str, Any]] = []
    for service_name, service_data in services.items():
        if not isinstance(service_data, dict):
            continue
        status = str(service_data.get("status") or "unknown")
        rows.append(
            {
                "service": service_name,
                "name": str(service_data.get("name") or service_name),
                "status": status,
                "state": status.lower(),
                "health": service_data.get("health"),
                "run_id": run_id,
                "source": "orchestrator",
            }
        )
    return rows


@router.get("/system")
async def get_system_status():
    """Get overall system health and status.
    
    Returns:
        System status including Docker availability, paths, and configuration.
    """
    orchestrator_probe = _probe_orchestrator_health()
    
    # Check compose files exist from runtime descriptors (with fallback if enabled)
    environment_launches = run_launcher.list_environment_launches()
    compose_files = {
        "core": f"{settings.compose_base_path}/core.yml",
    }

    for environment_id, runtime in environment_launches.items():
        environment_service = runtime.get("environment_service") if isinstance(runtime, dict) else None
        if isinstance(environment_service, dict) and environment_service.get("compose_file"):
            compose_files[f"environment_{environment_id}"] = (
                f"{settings.compose_base_path}/{environment_service['compose_file']}"
            )

        agent_service = runtime.get("agent_worker_service") if isinstance(runtime, dict) else None
        if isinstance(agent_service, dict) and agent_service.get("compose_file"):
            compose_files[f"agents_{environment_id}"] = (
                f"{settings.compose_base_path}/{agent_service['compose_file']}"
            )

    compose_status = {}
    import os
    for name, path in compose_files.items():
        compose_status[name] = {
            "path": path,
            "exists": os.path.exists(path),
            "absolute": os.path.abspath(path)
        }
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "docker": {
            "available": None,
            "error": "direct docker probe disabled in controller container",
            "source": "orchestrator",
        },
        "compose": {
            "base_path": settings.compose_base_path,
            "absolute_base_path": os.path.abspath(settings.compose_base_path),
            "files": compose_status,
        },
        "orchestrator": {
            "url": settings.orchestrator_url,
            "probe": orchestrator_probe,
        },
        "environment_url_pattern": settings.environment_url_pattern,
    }


@router.get("/containers")
async def get_container_status(db: Session = Depends(get_db)):
    """Get status of known run-scoped containers via orchestrator metadata.
    
    Returns:
        Container-like records derived from orchestrator run status payloads.
    """
    try:
        runs = db.query(Run).order_by(Run.started_at.desc()).limit(200).all()
        per_run_containers: List[Dict[str, Any]] = []
        probe_errors: List[Dict[str, str]] = []

        for run in runs:
            status_payload = run_launcher.get_run_status(run.run_id)
            status_value = str(status_payload.get("status") or "").strip().lower()
            if status_value in {"error", ""}:
                probe_errors.append(
                    {
                        "run_id": run.run_id,
                        "error": str(status_payload.get("error") or "unknown orchestrator status error"),
                    }
                )
            per_run_containers.extend(
                _extract_orchestrator_run_containers(run.run_id, status_payload)
            )

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "container_count": len(per_run_containers),
            "core_containers": [],
            "per_run_containers": per_run_containers,
            "other_containers": [],
            "source": "orchestrator",
            "probe_errors": probe_errors,
            "sampled_runs": len(runs),
        }
    except Exception as e:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e),
            "containers": [],
            "source": "orchestrator",
        }


@router.get("/runs/{run_id}/diagnostics")
async def get_run_diagnostics(run_id: str, db: Session = Depends(get_db)):
    """Get detailed diagnostics for a specific run.
    
    Args:
        run_id: Run identifier
        db: Database session
        
    Returns:
        Run status, container info, event count, and potential issues.
    """
    # Get run from database
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    # Get event count
    event_count = db.query(Event).filter(Event.run_id == run_id).count()
    context = run_binding.build_run_context(run, db)
    
    # Query run-scoped service/container visibility from orchestrator metadata.
    orchestrator_status = run_launcher.get_run_status(run_id)
    orchestrator_state = str(orchestrator_status.get("status") or "").strip().lower()
    containers = _extract_orchestrator_run_containers(run_id, orchestrator_status)
    
    # Check for override file
    import os
    override_path = f"{run_launcher.overrides_path}/{run_id}.yml"
    override_exists = os.path.exists(override_path)
    
    # Identify issues
    issues = []
    if run.status == "running" and not containers:
        issues.append("Run marked as running but no containers found")
    if orchestrator_state == "error":
        issues.append("Orchestrator status probe failed for this run")
    if orchestrator_state == "not_found" and run.status in ["running", "paused", "completed", "timed_out"]:
        issues.append("Run stack not found in orchestrator for non-terminal/active-facing run state")
    if event_count == 0 and run.status in ["running", "completed", "timed_out"]:
        issues.append("No events ingested for this run")
    if not override_exists and run.status in ["running", "completed", "timed_out"]:
        issues.append("Override file missing - containers may not have been created properly")
    
    return {
        "run_id": run_id,
        "environment_id": context.get("environment_id"),
        "runtime_id": context.get("runtime_id"),
        "snapshot_hash": context.get("snapshot_hash"),
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "institutional_mode": run.institutional_mode,
        "event_count": event_count,
        "containers": containers,
        "container_source": "orchestrator",
        "orchestrator_status": orchestrator_status,
        "override_file_exists": override_exists,
        "override_file_path": override_path,
        "issues": issues,
        "diagnosed_at": datetime.utcnow().isoformat(),
    }


@router.get("/events/pipeline")
async def get_event_pipeline_status(db: Session = Depends(get_db)):
    """Get status of the event pipeline.
    
    Returns:
        Event counts by run, recent events, and pipeline health.
    """
    # Get event counts by run
    from sqlalchemy import func
    
    run_counts = (
        db.query(Event.run_id, func.count(Event.event_id).label("count"))
        .group_by(Event.run_id)
        .all()
    )
    
    # Get recent events
    recent_events = (
        db.query(Event)
        .order_by(Event.timestamp.desc())
        .limit(10)
        .all()
    )
    
    # Get total event count
    total_events = db.query(Event).count()
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "total_events": total_events,
        "events_by_run": [{"run_id": r[0], "count": r[1]} for r in run_counts],
        "recent_events": [
            {
                "event_id": e.event_id,
                "run_id": e.run_id,
                "event_type": e.event_type,
                "action_name": e.action_name,
                "agent_id": e.agent_id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in recent_events
        ],
        "pipeline_healthy": total_events > 0 or len(run_counts) == 0,
    }
