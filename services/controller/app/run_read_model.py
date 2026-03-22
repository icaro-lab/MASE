"""Run read-model helpers for the public controller surface."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.catalog import candidate_environment_roots, get_environment
from app.config import settings
from app.database import Run as RunDB
from app.database import RunEnvironmentAssignment as RunEnvironmentAssignmentDB
from app.database import RunStatus as RunStatusEnum
from app.models import Run, RunStatus
from app.run_binding import build_run_context
from app.run_config import resolve_run_runtime_limit
from app.run_launcher import RunLaunchConfig, run_launcher


logger = logging.getLogger(__name__)
TERMINAL_RUN_STATUSES = {
    RunStatusEnum.COMPLETED.value,
    RunStatusEnum.TIMED_OUT.value,
    RunStatusEnum.FAILED.value,
    RunStatusEnum.CANCELLED.value,
}


def _context_has_frontend(context: dict[str, Any]) -> bool:
    launch = context.get("launch") if isinstance(context.get("launch"), dict) else {}
    frontend_service = launch.get("frontend_service")
    if isinstance(frontend_service, dict) and bool(str(frontend_service.get("service_name") or "").strip()):
        return True

    environment_ref = str(context.get("environment_ref") or context.get("environment_id") or "").strip()
    environment_id = normalize_environment_ref(environment_ref)
    if not environment_id:
        return False
    manifest = get_environment(environment_id, candidate_environment_roots())
    if not isinstance(manifest, dict):
        return False
    manifest_launch = manifest.get("launch") if isinstance(manifest.get("launch"), dict) else {}
    manifest_frontend = manifest_launch.get("frontend_service")
    return isinstance(manifest_frontend, dict) and bool(str(manifest_frontend.get("service_name") or "").strip())


def as_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize naive/aware datetimes to timezone-aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_environment_ref(environment_ref: str | None) -> str:
    """Normalize environment refs to bare environment id."""
    cleaned = str(environment_ref or "").strip().strip("/")
    if cleaned.startswith("environment/"):
        cleaned = cleaned[len("environment/") :]
    if cleaned.startswith("environments/"):
        cleaned = cleaned[len("environments/") :]

    normalized = cleaned.split("/")[-1] or cleaned
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


def resolve_environment_id_for_run(run: RunDB, db: Session) -> str:
    context = build_run_context(run, db)
    bound_environment_ref = str(context.get("environment_ref") or "").strip()
    if bound_environment_ref:
        normalized = normalize_environment_ref(bound_environment_ref)
        if normalized:
            return normalized

    bound_environment_id = str(context.get("environment_id") or "").strip()
    if bound_environment_id:
        return bound_environment_id

    assignment = (
        db.query(RunEnvironmentAssignmentDB)
        .filter(RunEnvironmentAssignmentDB.run_id == run.run_id)
        .first()
    )
    if assignment and assignment.environment_id:
        return str(assignment.environment_id)

    fallback_environment = settings.get_default_environment_id()
    logger.warning(
        "run_environment_assignment_missing run_id=%s fallback_environment=%s",
        run.run_id,
        fallback_environment,
    )
    return fallback_environment


def resolve_run_service_urls(run: RunDB, db: Session) -> dict[str, str]:
    context = build_run_context(run, db)
    environment_id = resolve_environment_id_for_run(run, db)
    has_frontend = _context_has_frontend(context)
    try:
        return run_launcher.get_service_urls(
            run_id=run.run_id,
            environment_id=environment_id,
            institutional_mode=bool(run.institutional_mode),
        )
    except Exception as exc:
        logger.warning(
            "run_service_url_resolution_fallback run_id=%s environment=%s error=%s",
            run.run_id,
            environment_id,
            exc,
        )
        fallback_urls: dict[str, str] = {
            "environment": settings.get_environment_url(run.run_id, environment_id=environment_id),
            "agent_worker": settings.get_agent_worker_url(run.run_id, environment_id=environment_id),
        }
        if has_frontend:
            fallback_urls["environment_frontend"] = run_launcher.get_environment_frontend_url(run.run_id)
        return fallback_urls


def build_restart_launch_config(run: RunDB, db: Session) -> RunLaunchConfig:
    context = build_run_context(run, db)
    environment_id = resolve_environment_id_for_run(run, db)
    effective_api_key = settings.openrouter_api_key or None

    return RunLaunchConfig(
        run_id=run.run_id,
        environment_id=str(context.get("environment_id") or environment_id),
        institutional_mode=bool(run.institutional_mode),
        seed=run.seed,
        resolved_bundle_hash=(
            str(getattr(run, "resolved_bundle_hash", "") or "").strip()
            or None
        ),
        api_key=effective_api_key,
    )


def build_run_response(
    run: RunDB,
    db: Session,
    *,
    agent_count: int | None = None,
    initialized_agents: list[str] | None = None,
) -> Run:
    context = build_run_context(run, db)
    runtime_limit_minutes, _ = resolve_run_runtime_limit(context.get("environment_config"))
    has_frontend = _context_has_frontend(context)

    service_urls = resolve_run_service_urls(run, db)
    environment_id = resolve_environment_id_for_run(run, db)
    environment_url = service_urls.get("environment") or settings.get_environment_url(
        run.run_id,
        environment_id=environment_id,
    )
    frontend_url = None
    if has_frontend:
        frontend_url = service_urls.get("environment_frontend") or run_launcher.get_environment_frontend_url(run.run_id)
    return Run(
        run_id=run.run_id,
        resolved_bundle_hash=run.resolved_bundle_hash,
        environment_url=environment_url,
        frontend_url=frontend_url,
        status=RunStatus(run.status),
        institutional_mode=run.institutional_mode,
        seed=run.seed,
        started_at=as_utc_datetime(run.started_at),
        ended_at=as_utc_datetime(run.ended_at),
        runtime_limit_minutes=runtime_limit_minutes,
        agent_count=agent_count,
        initialized_agents=initialized_agents,
        experiment_policy_hash=str(getattr(run, "experiment_policy_hash", "") or "") or None,
        experiment_manifest_hash=str(getattr(run, "experiment_manifest_hash", "") or "") or None,
        experiment_assignment_hash=str(getattr(run, "experiment_assignment_hash", "") or "") or None,
        experiment_policy_state=str(getattr(run, "experiment_policy_state", "") or "") or None,
    )
