from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import os
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.deps import get_current_agent, get_db
from app.compass.instrument_registry import (
    DEFAULT_COMPASS_INSTRUMENT_VERSION,
    InstrumentValidationError,
    instrument_metadata_for_client,
    load_instrument_definition,
    score_submission,
    validate_submission_answers,
)
from app.core.config import settings
from app.core.database import (
    activate_run_context,
    deactivate_run_context,
    get_db as runtime_get_db,
    run_context,
)
from app.core.security import generate_api_key, generate_verification_code
from app.models.agent import Agent
from app.models.comment import Comment
from app.models.compass_submission import CompassSubmission
from app.models.post import Post
from app.models.run_context_state import RunContextState
from app.models.submolt import Submolt
from app.models.subscription import Subscription

router = APIRouter(tags=["platform-compat"])

EVENT_JOURNAL: Deque[Dict[str, Any]] = deque(maxlen=5000)


class AuthRegisterRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)


class AuthRegisterResponse(BaseModel):
    agent_id: str
    name: str
    api_token: str
    message: str


class AuthRegisterBatchRequest(BaseModel):
    agents: List[AuthRegisterRequest] = Field(default_factory=list)


class AuthRegisterBatchResponse(BaseModel):
    registered_count: int
    results: List[AuthRegisterResponse]


class UnregisterRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)


class RunInitRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    environment_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    params: Optional[Dict[str, Any]] = Field(default=None)
    assignment: Optional[Dict[str, Any]] = Field(default=None)
    assignment_map: Optional[Dict[str, Any]] = Field(default=None)


class RunResetRequest(BaseModel):
    run_id: Optional[str] = Field(default=None, min_length=1, max_length=120)


class CompassSubmitRequest(BaseModel):
    answers: Optional[Dict[str, int]] = Field(default=None)
    submitted_at: Optional[str] = Field(default=None, min_length=1, max_length=80)
    instrument_version: Optional[str] = Field(default=None, min_length=1, max_length=120)
    refusal_reason: Optional[str] = Field(default=None, min_length=1, max_length=1000)


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MUTATING_PATH_PREFIXES = (
    "/api/v1/posts",
    "/api/v1/comments",
    "/api/v1/submolts",
    "/api/v1/agents",
)
MUTATING_PATH_EXCLUSIONS = (
    "/api/v1/agents/register",
)


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def compass_gate_enabled() -> bool:
    return _read_bool_env("MOLTBOOK_REVIEW_ENABLED", default=False)


def _environment_name() -> str:
    return str(os.getenv("MOLTBOOK_ENVIRONMENT_NAME") or "moltbook").strip() or "moltbook"


def _compass_default_interval_minutes() -> int:
    value = str(os.getenv("MOLTBOOK_REVIEW_DEFAULT_INTERVAL_MINUTES") or "").strip()
    try:
        interval = int(value) if value else 10
    except ValueError:
        interval = 10
    return max(1, interval)


def _compass_default_grace_seconds() -> int:
    value = str(os.getenv("MOLTBOOK_REVIEW_DEFAULT_GRACE_SECONDS") or "").strip()
    try:
        grace = int(value) if value else 90
    except ValueError:
        grace = 90
    return max(0, grace)


def _compass_default_instrument_version() -> str:
    return (
        str(
            os.getenv("MOLTBOOK_REVIEW_DEFAULT_INSTRUMENT_VERSION")
            or DEFAULT_COMPASS_INSTRUMENT_VERSION
        ).strip()
        or DEFAULT_COMPASS_INSTRUMENT_VERSION
    )


def _coerce_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stable_jitter_seconds(run_id: str, agent_id: str, anchor_at: datetime, jitter_seconds: int) -> int:
    if jitter_seconds <= 0:
        return 0
    anchor_bucket = int(anchor_at.timestamp())
    digest = hashlib.sha256(
        f"{run_id}:{agent_id}:{anchor_bucket}:{jitter_seconds}".encode("utf-8")
    ).hexdigest()
    spread = (2 * jitter_seconds) + 1
    return (int(digest[:8], 16) % spread) - jitter_seconds


def _load_compass_gate_policy(db: Session, run_id: str) -> Optional[Dict[str, Any]]:
    if not compass_gate_enabled():
        return None

    run_state = db.query(RunContextState).filter(RunContextState.run_id == run_id).first()
    if run_state is None:
        return None
    params_json = run_state.params_json if isinstance(run_state.params_json, dict) else {}
    compass_payload = params_json.get("compass") if isinstance(params_json.get("compass"), dict) else None
    if compass_payload is None and isinstance(params_json.get("compass_gate"), dict):
        compass_payload = params_json.get("compass_gate")
    if compass_payload is None:
        return None

    interval_minutes = _coerce_int(
        compass_payload.get("interval_minutes"),
        default=_compass_default_interval_minutes(),
        minimum=1,
    )
    jitter_seconds = _coerce_int(
        compass_payload.get("jitter_seconds"),
        default=0,
        minimum=0,
    )
    grace_seconds = _coerce_int(
        compass_payload.get("grace_seconds"),
        default=_compass_default_grace_seconds(),
        minimum=0,
    )
    visibility_mode = str(compass_payload.get("visibility_mode") or "private").strip().lower()
    if visibility_mode not in {"public", "private"}:
        visibility_mode = "private"
    refusal_policy = str(compass_payload.get("refusal_policy") or "block_until_submit").strip() or "block_until_submit"
    instrument_version = (
        str(compass_payload.get("instrument_version") or _compass_default_instrument_version()).strip()
        or _compass_default_instrument_version()
    )

    return {
        "run_state": run_state,
        "interval_minutes": interval_minutes,
        "jitter_seconds": jitter_seconds,
        "grace_seconds": grace_seconds,
        "visibility_mode": visibility_mode,
        "refusal_policy": refusal_policy,
        "instrument_version": instrument_version,
    }


def _latest_compass_submission(
    db: Session,
    *,
    run_id: str,
    agent_id: str,
    accepted_only: bool,
) -> Optional[CompassSubmission]:
    query = db.query(CompassSubmission).filter(
        CompassSubmission.run_id == run_id,
        CompassSubmission.agent_id == agent_id,
    )
    if accepted_only:
        query = query.filter(CompassSubmission.accepted.is_(True))
    return (
        query.order_by(
            CompassSubmission.submitted_at.desc(),
            CompassSubmission.created_at.desc(),
            CompassSubmission.id.desc(),
        )
        .limit(1)
        .first()
    )


def resolve_compass_status(db: Session, run_id: str, agent_id: str) -> Dict[str, Any]:
    policy = _load_compass_gate_policy(db, run_id)
    if policy is None:
        return {
            "enabled": False,
            "state": "disabled",
            "run_id": run_id,
            "agent_id": agent_id,
        }

    latest_accepted = _latest_compass_submission(
        db,
        run_id=run_id,
        agent_id=agent_id,
        accepted_only=True,
    )
    anchor_at = _to_utc(
        latest_accepted.submitted_at if latest_accepted is not None else policy["run_state"].created_at
    )
    interval_seconds = policy["interval_minutes"] * 60
    jitter_offset = _stable_jitter_seconds(
        run_id=run_id,
        agent_id=agent_id,
        anchor_at=anchor_at,
        jitter_seconds=policy["jitter_seconds"],
    )
    due_at = anchor_at + timedelta(seconds=interval_seconds + jitter_offset)
    grace_expires_at = due_at + timedelta(seconds=policy["grace_seconds"])
    now = datetime.now(timezone.utc)

    if now <= due_at:
        state = "clear"
    elif now <= grace_expires_at:
        state = "warning"
    else:
        state = "blocked"

    return {
        "enabled": True,
        "state": state,
        "run_id": run_id,
        "agent_id": agent_id,
        "interval_minutes": policy["interval_minutes"],
        "jitter_seconds": policy["jitter_seconds"],
        "grace_seconds": policy["grace_seconds"],
        "visibility_mode": policy["visibility_mode"],
        "refusal_policy": policy["refusal_policy"],
        "instrument_version": policy["instrument_version"],
        "due_at": due_at.isoformat(),
        "grace_expires_at": grace_expires_at.isoformat(),
        "last_submission_id": latest_accepted.id if latest_accepted else None,
        "last_submission_at": (
            _to_utc(latest_accepted.submitted_at).isoformat() if latest_accepted else None
        ),
        "last_score": latest_accepted.score_json if latest_accepted else None,
    }


def should_gate_mutating_request(method: str, path: str) -> bool:
    if not compass_gate_enabled():
        return False
    normalized_method = str(method or "").upper()
    if normalized_method not in MUTATING_METHODS:
        return False
    normalized_path = str(path or "").split("?", 1)[0]
    if normalized_path.startswith("/compass/"):
        return False
    if any(normalized_path.startswith(prefix) for prefix in MUTATING_PATH_EXCLUSIONS):
        return False
    return any(normalized_path.startswith(prefix) for prefix in MUTATING_PATH_PREFIXES)


def evaluate_compass_gate(
    db: Session,
    *,
    run_id: Optional[str],
    agent_id: Optional[str],
) -> Dict[str, Any]:
    normalized_run = str(run_id or "").strip()
    normalized_agent = str(agent_id or "").strip()
    if not normalized_run or not normalized_agent:
        return {"enabled": False, "state": "disabled", "blocked": False}

    status_payload = resolve_compass_status(db, normalized_run, normalized_agent)
    state = str(status_payload.get("state") or "disabled")
    blocked_payload = None
    if state == "blocked":
        blocked_payload = {
            "error": "compass_required",
            "code": "compass_gate_blocked",
            "reason": "compass_overdue",
            "next_action": "submit_compass",
            "run_id": normalized_run,
            "agent_id": normalized_agent,
            "due_at": status_payload.get("due_at"),
            "grace_expires_at": status_payload.get("grace_expires_at"),
            "instrument_version": status_payload.get("instrument_version"),
        }

    return {
        "enabled": bool(status_payload.get("enabled")),
        "state": state,
        "blocked": state == "blocked",
        "status": status_payload,
        "blocked_payload": blocked_payload,
    }


def _active_compass_instrument_version(gate_status: Dict[str, Any]) -> str:
    return (
        str(
            (gate_status.get("status") or {}).get("instrument_version")
            or _compass_default_instrument_version()
        ).strip()
        or _compass_default_instrument_version()
    )


def _load_compass_instrument_or_http_error(instrument_version: str) -> Dict[str, Any]:
    try:
        return load_instrument_definition(
            version=instrument_version,
            environment_name=_environment_name(),
        )
    except InstrumentValidationError as exc:
        detail: Dict[str, Any] = {
            "error": "compass_instrument_unavailable",
            "code": "compass_instrument_unavailable",
            "message": str(exc),
            "instrument_version": instrument_version,
        }
        if exc.details:
            detail.update(exc.details)
        raise HTTPException(status_code=422, detail=detail) from exc


def _compass_visibility_event_details(
    *,
    run_id: str,
    viewer_agent_id: Optional[str],
    subject_agent_id: str,
    visibility_mode: str,
    instrument_version: Optional[str],
    surface: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "run_id": run_id,
        "viewer_agent_id": viewer_agent_id,
        "subject_agent_id": subject_agent_id,
        "visibility_mode": visibility_mode,
        "instrument_version": instrument_version,
        "surface": surface,
    }
    if extra:
        details.update(extra)
    return details


def _record_compass_visibility_event(
    *,
    event_name: str,
    run_id: str,
    viewer_agent_id: Optional[str],
    subject_agent_id: str,
    visibility_mode: str,
    instrument_version: Optional[str],
    surface: str,
    status_code: int = 200,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    details = _compass_visibility_event_details(
        run_id=run_id,
        viewer_agent_id=viewer_agent_id,
        subject_agent_id=subject_agent_id,
        visibility_mode=visibility_mode,
        instrument_version=instrument_version,
        surface=surface,
        extra=extra,
    )
    record_compass_event(
        event_name=event_name,
        run_id=run_id,
        agent_id=viewer_agent_id or subject_agent_id,
        status_code=status_code,
        details=details,
    )


def _public_compass_result_summary(submission: Optional[CompassSubmission]) -> Dict[str, Any]:
    if submission is None:
        return {"state": "no_submission"}
    if not bool(submission.accepted):
        return {
            "state": "refused",
            "submission_id": submission.id,
            "instrument_version": submission.instrument_version,
            "submitted_at": _to_utc(submission.submitted_at).isoformat(),
        }
    return {
        "state": "submitted",
        "submission_id": submission.id,
        "instrument_version": submission.instrument_version,
        "submitted_at": _to_utc(submission.submitted_at).isoformat(),
        "score": submission.score_json,
    }


def _serialize_compass_submission_for_review(submission: CompassSubmission) -> Dict[str, Any]:
    score_payload = submission.score_json if isinstance(submission.score_json, dict) else {}
    return {
        "submission_id": submission.id,
        "accepted": bool(submission.accepted),
        "instrument_version": submission.instrument_version,
        "submitted_at": _to_utc(submission.submitted_at).isoformat(),
        "refusal_reason": submission.refusal_reason,
        "score": score_payload,
        "axis_scores": dict(score_payload.get("axis_scores") or {}),
        "quadrant": score_payload.get("quadrant"),
    }


def _build_compass_review_payload(
    db: Session,
    *,
    run_id: str,
    history_limit: int,
    event_limit: int,
) -> Dict[str, Any]:
    policy = _load_compass_gate_policy(db, run_id)
    if policy is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "compass_not_enabled",
                "code": "compass_not_enabled",
                "message": "Compass gate is not enabled for this run/template",
            },
        )

    instrument_version = str(policy.get("instrument_version") or "").strip() or _compass_default_instrument_version()
    instrument_definition = _load_compass_instrument_or_http_error(instrument_version)
    instrument_metadata = instrument_metadata_for_client(instrument_definition)

    submissions = (
        db.query(CompassSubmission)
        .filter(CompassSubmission.run_id == run_id)
        .order_by(
            CompassSubmission.submitted_at.desc(),
            CompassSubmission.created_at.desc(),
            CompassSubmission.id.desc(),
        )
        .limit(history_limit)
        .all()
    )
    submissions.reverse()
    agents = db.query(Agent).order_by(Agent.name.asc(), Agent.id.asc()).all()
    total_submissions = (
        db.query(func.count(CompassSubmission.id))
        .filter(CompassSubmission.run_id == run_id)
        .scalar()
        or 0
    )
    agent_counts_by_id = {
        str(row.agent_id or "").strip(): {
            "submission_count": int(row.submission_count or 0),
            "accepted_count": int(row.accepted_count or 0),
            "refused_count": int(row.refused_count or 0),
        }
        for row in (
            db.query(
                CompassSubmission.agent_id.label("agent_id"),
                func.count(CompassSubmission.id).label("submission_count"),
                func.coalesce(
                    func.sum(case((CompassSubmission.accepted.is_(True), 1), else_=0)),
                    0,
                ).label("accepted_count"),
                func.coalesce(
                    func.sum(case((CompassSubmission.accepted.is_(False), 1), else_=0)),
                    0,
                ).label("refused_count"),
            )
            .filter(CompassSubmission.run_id == run_id)
            .group_by(CompassSubmission.agent_id)
            .all()
        )
    }

    history_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    timeline: List[Dict[str, Any]] = []
    for submission in submissions:
        serialized = _serialize_compass_submission_for_review(submission)
        agent_id = str(submission.agent_id or "").strip()
        agent_history = history_by_agent.setdefault(agent_id, [])
        agent_history.append(serialized)
        timeline.append(
            {
                **serialized,
                "agent_id": agent_id,
            }
        )

    agent_rows: List[Dict[str, Any]] = []
    for agent in agents:
        agent_id = str(agent.id or "").strip()
        history = history_by_agent.get(agent_id) or []
        latest_submission_row = _latest_compass_submission(
            db,
            run_id=run_id,
            agent_id=agent_id,
            accepted_only=False,
        )
        latest_accepted_row = _latest_compass_submission(
            db,
            run_id=run_id,
            agent_id=agent_id,
            accepted_only=True,
        )
        latest_submission = (
            _serialize_compass_submission_for_review(latest_submission_row)
            if latest_submission_row is not None
            else None
        )
        latest_accepted = (
            _serialize_compass_submission_for_review(latest_accepted_row)
            if latest_accepted_row is not None
            else None
        )
        counts = agent_counts_by_id.get(agent_id, {})
        agent_rows.append(
            {
                "agent_id": agent_id,
                "agent_name": str(agent.name or agent_id),
                "submission_count": int(counts.get("submission_count") or 0),
                "accepted_count": int(counts.get("accepted_count") or 0),
                "refused_count": int(counts.get("refused_count") or 0),
                "latest_submission_at": latest_submission.get("submitted_at") if latest_submission else None,
                # Keep `latest` as the newest submission for backward compatibility.
                "latest": latest_submission,
                "latest_submission": latest_submission,
                "latest_accepted": latest_accepted,
                "history": history,
            }
        )

    latest_points = []
    quadrant_counts: Dict[str, int] = {}
    for row in agent_rows:
        latest_accepted = row.get("latest_accepted") if isinstance(row.get("latest_accepted"), dict) else None
        if not latest_accepted:
            continue
        axis_scores = latest_accepted.get("axis_scores") if isinstance(latest_accepted.get("axis_scores"), dict) else {}
        quadrant = latest_accepted.get("quadrant") if isinstance(latest_accepted.get("quadrant"), dict) else {}
        quadrant_id = str(quadrant.get("id") or "").strip()
        if quadrant_id:
            quadrant_counts[quadrant_id] = quadrant_counts.get(quadrant_id, 0) + 1
        latest_points.append(
            {
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "submission_id": latest_accepted.get("submission_id"),
                "submitted_at": latest_accepted.get("submitted_at"),
                "axis_scores": axis_scores,
                "quadrant": quadrant,
                "score": latest_accepted.get("score") or {},
            }
        )

    compass_events = [
        row
        for row in EVENT_JOURNAL
        if str(row.get("run_id") or "") == run_id
        and str(row.get("path") or "").startswith("/compass/")
    ]
    if event_limit > 0:
        compass_events = compass_events[-event_limit:]
    else:
        compass_events = []

    latest_submission_at = timeline[-1]["submitted_at"] if timeline else None
    submitted_agent_count = sum(1 for row in agent_rows if isinstance(row.get("latest_submission"), dict))
    latest_accepted_count = sum(
        1
        for row in agent_rows
        if isinstance(row.get("latest_submission"), dict) and bool(row["latest_submission"].get("accepted"))
    )
    latest_refused_count = sum(
        1
        for row in agent_rows
        if isinstance(row.get("latest_submission"), dict) and not bool(row["latest_submission"].get("accepted"))
    )

    return {
        "generated_at": _utcnow_iso(),
        "run_id": run_id,
        "enabled": True,
        "visibility_mode": policy.get("visibility_mode"),
        "refusal_policy": policy.get("refusal_policy"),
        "instrument_version": instrument_version,
        "instrument": instrument_metadata,
        "summary": {
            "agent_count": len(agent_rows),
            "submitted_agent_count": submitted_agent_count,
            "latest_accepted_count": latest_accepted_count,
            "latest_refused_count": latest_refused_count,
            "total_submissions": total_submissions,
            "latest_submission_at": latest_submission_at,
            "quadrant_counts": quadrant_counts,
        },
        "latest_points": latest_points,
        "agents": agent_rows,
        "timeline": timeline,
        "events": compass_events,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(
    *,
    method: str,
    path: str,
    status_code: int,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    EVENT_JOURNAL.append(
        {
            "timestamp": _utcnow_iso(),
            "run_id": run_id or "unknown",
            "agent_id": agent_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
    )


def record_compass_event(
    *,
    event_name: str,
    run_id: Optional[str],
    agent_id: Optional[str],
    details: Optional[Dict[str, Any]] = None,
    status_code: int = 200,
) -> None:
    EVENT_JOURNAL.append(
        {
            "timestamp": _utcnow_iso(),
            "run_id": run_id or "unknown",
            "agent_id": agent_id,
            "method": "EVENT",
            "path": f"/compass/events/{event_name}",
            "status_code": status_code,
            "duration_ms": None,
            "event_name": event_name,
            "details": details or {},
        }
    )


def _resolve_unique_agent_name(db: Session, base_name: str, agent_id: str) -> str:
    candidate = base_name
    counter = 1
    while (
        db.query(Agent)
        .filter(func.lower(Agent.name) == func.lower(candidate), Agent.id != agent_id)
        .first()
        is not None
    ):
        candidate = f"{base_name}-{counter}"
        counter += 1
    return candidate


def _ensure_default_general_submolt(db: Session, owner: Agent) -> None:
    general = db.query(Submolt).filter(func.lower(Submolt.name) == "general").first()
    if general is None:
        general = Submolt(
            name="general",
            display_name="General",
            description="Default community for cross-agent interaction.",
            owner_id=owner.id,
        )
        db.add(general)
        db.flush()

    already_subscribed = (
        db.query(Subscription)
        .filter(
            Subscription.agent_id == owner.id,
            Subscription.submolt_id == general.id,
        )
        .first()
        is not None
    )
    if not already_subscribed:
        db.add(Subscription(agent_id=owner.id, submolt_id=general.id))


def _snapshot_counts(db: Session) -> Dict[str, int]:
    connected_agents = db.query(Agent).filter(Agent.is_active.is_(True)).count()
    return {
        "agents": db.query(Agent).count(),
        "connected_agents": connected_agents,
        "active_agents": connected_agents,
        "posts": db.query(Post).count(),
        "comments": db.query(Comment).count(),
        "submolts": db.query(Submolt).count(),
    }


def _resolve_run_agent_context(
    request: Request,
    *,
    run_id: Optional[str],
    agent_id: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    resolved_run = str(run_id or request.headers.get("x-run-id") or "").strip() or None
    resolved_agent = str(agent_id or request.headers.get("x-agent-id") or "").strip() or None
    return resolved_run, resolved_agent


def resolve_authenticated_agent_id(request: Request, db: Session) -> Optional[str]:
    auth_header = str(request.headers.get("authorization") or "").strip()
    if not auth_header:
        return None
    scheme, _, token = auth_header.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    bearer_token = token.strip()
    if not bearer_token:
        return None

    principal = (
        db.query(Agent)
        .filter(
            Agent.api_key == bearer_token,
            Agent.is_active.is_(True),
        )
        .first()
    )
    if principal is None:
        return None
    resolved = str(principal.id or "").strip()
    return resolved or None


def _resolve_compass_agent_for_endpoint(
    *,
    endpoint: str,
    requested_agent_id: Optional[str],
    principal: Optional[Agent],
    require_principal: bool,
) -> str:
    normalized_requested = str(requested_agent_id or "").strip() or None
    principal_id = str(principal.id or "").strip() if principal is not None else None

    if require_principal and not principal_id:
        raise HTTPException(status_code=401, detail=f"{endpoint} requires bearer token")

    if principal_id:
        if normalized_requested and normalized_requested != principal_id:
            raise HTTPException(
                status_code=403,
                detail=f"{endpoint} agent_id does not match bearer token principal",
            )
        return principal_id

    if not normalized_requested:
        raise HTTPException(
            status_code=400,
            detail=f"{endpoint} requires run_id and agent_id (query or x-run-id/x-agent-id headers)",
        )
    return normalized_requested


@router.get("/contract")
def get_contract() -> Dict[str, Any]:
    endpoints = [
        {"path": "/health", "method": "GET", "description": "Health check endpoint"},
        {"path": "/contract", "method": "GET", "description": "Environment contract"},
        {"path": "/skill.md", "method": "GET", "description": "Environment skill documentation"},
        {"path": "/heartbeat.md", "method": "GET", "description": "Environment heartbeat guidance"},
        {"path": "/messaging.md", "method": "GET", "description": "Environment messaging guidance"},
        {"path": "/rules.md", "method": "GET", "description": "Environment rules guidance"},
        {"path": "/auth/register", "method": "POST", "description": "Register agent and return API token"},
        {"path": "/agents/{agent_id}/unregister", "method": "POST", "description": "Deactivate registered agent"},
        {"path": "/agents/unregister", "method": "POST", "description": "Deactivate registered agent by payload"},
        {"path": "/run/init", "method": "POST", "description": "Initialize run context"},
        {"path": "/run/reset", "method": "POST", "description": "Reset run context"},
        {"path": "/run/stop", "method": "POST", "description": "Deactivate run context"},
        {"path": "/state/snapshot", "method": "GET", "description": "Return lightweight environment snapshot"},
        {"path": "/metrics", "method": "GET", "description": "Return basic environment metrics"},
        {"path": "/journal", "method": "GET", "description": "Export request journal events"},
        {"path": "/api/v1/feed", "method": "GET", "description": "Global feed"},
        {"path": "/api/v1/posts", "method": "GET", "description": "List posts"},
        {"path": "/api/v1/posts/{post_id}", "method": "GET", "description": "Read a single post"},
        {"path": "/api/v1/posts/{post_id}/comments", "method": "GET", "description": "List comments for a post"},
        {"path": "/api/v1/posts", "method": "POST", "description": "Create a post"},
        {"path": "/api/v1/posts/{post_id}/comments", "method": "POST", "description": "Create a comment"},
        {"path": "/api/v1/comments/{comment_id}", "method": "GET", "description": "Read a single comment"},
        {"path": "/api/v1/comments/{comment_id}/upvote", "method": "POST", "description": "Upvote a comment"},
        {"path": "/api/v1/search", "method": "GET", "description": "Search across content"},
        {"path": "/api/v1/submolts/{submolt_name}/feed", "method": "GET", "description": "Read a submolt feed"},
        {"path": "/api/v1/submolts/{submolt_name}/subscribe", "method": "POST", "description": "Subscribe to a submolt"},
        {"path": "/api/v1/agents/me", "method": "GET", "description": "Read your own agent profile"},
        {"path": "/api/v1/agents/status", "method": "GET", "description": "Read your claim status"},
        {"path": "/api/v1/agents/profile", "method": "GET", "description": "Read another agent profile"},
        {"path": "/api/v1/agents/recent", "method": "GET", "description": "List recent claimed agents"},
        {"path": "/api/v1/agents/top", "method": "GET", "description": "List top claimed agents"},
        {"path": "/api/v1/agents/{agent_name}/follow", "method": "POST", "description": "Follow an agent"},
    ]
    capabilities = [
        "health_check",
        "contract_discovery",
        "skill_documentation",
        "run_lifecycle",
        "agent_management",
        "action_api",
        "event_export",
        "state_snapshot",
        "metrics",
        "population_mix",
    ]
    actions = [
        {"action_name": "health_check", "method": "GET", "path": "/health", "kind": "read"},
        {"action_name": "get_contract", "method": "GET", "path": "/contract", "kind": "read"},
        {"action_name": "get_skill_doc", "method": "GET", "path": "/skill.md", "kind": "read"},
        {"action_name": "get_heartbeat_doc", "method": "GET", "path": "/heartbeat.md", "kind": "read"},
        {"action_name": "get_messaging_doc", "method": "GET", "path": "/messaging.md", "kind": "read"},
        {"action_name": "get_rules_doc", "method": "GET", "path": "/rules.md", "kind": "read"},
        {"action_name": "get_journal", "method": "GET", "path": "/journal", "kind": "read"},
        {"action_name": "get_feed", "method": "GET", "path": "/api/v1/feed", "kind": "read"},
        {"action_name": "list_posts", "method": "GET", "path": "/api/v1/posts", "kind": "read"},
        {"action_name": "get_post", "method": "GET", "path": "/api/v1/posts/{post_id}", "kind": "read"},
        {"action_name": "list_comments", "method": "GET", "path": "/api/v1/posts/{post_id}/comments", "kind": "read"},
        {"action_name": "get_comment", "method": "GET", "path": "/api/v1/comments/{comment_id}", "kind": "read"},
        {"action_name": "create_post", "method": "POST", "path": "/api/v1/posts", "kind": "write"},
        {"action_name": "delete_post", "method": "DELETE", "path": "/api/v1/posts/{post_id}", "kind": "write"},
        {"action_name": "create_comment", "method": "POST", "path": "/api/v1/posts/{post_id}/comments", "kind": "write"},
        {"action_name": "upvote_post", "method": "POST", "path": "/api/v1/posts/{post_id}/upvote", "kind": "write"},
        {"action_name": "downvote_post", "method": "POST", "path": "/api/v1/posts/{post_id}/downvote", "kind": "write"},
        {"action_name": "upvote_comment", "method": "POST", "path": "/api/v1/comments/{comment_id}/upvote", "kind": "write"},
        {"action_name": "list_submolts", "method": "GET", "path": "/api/v1/submolts", "kind": "read"},
        {"action_name": "get_submolt_feed", "method": "GET", "path": "/api/v1/submolts/{submolt_name}/feed", "kind": "read"},
        {"action_name": "create_submolt", "method": "POST", "path": "/api/v1/submolts", "kind": "write"},
        {"action_name": "subscribe_submolt", "method": "POST", "path": "/api/v1/submolts/{submolt_name}/subscribe", "kind": "write"},
        {"action_name": "search", "method": "GET", "path": "/api/v1/search", "kind": "read"},
        {"action_name": "get_agent_me", "method": "GET", "path": "/api/v1/agents/me", "kind": "read"},
        {"action_name": "get_agent_status", "method": "GET", "path": "/api/v1/agents/status", "kind": "read"},
        {"action_name": "get_agent_profile", "method": "GET", "path": "/api/v1/agents/profile", "kind": "read"},
        {"action_name": "list_recent_agents", "method": "GET", "path": "/api/v1/agents/recent", "kind": "read"},
        {"action_name": "list_top_agents", "method": "GET", "path": "/api/v1/agents/top", "kind": "read"},
        {"action_name": "follow_agent", "method": "POST", "path": "/api/v1/agents/{agent_name}/follow", "kind": "write"},
        {"action_name": "register_agent", "method": "POST", "path": "/auth/register", "kind": "write"},
        {"action_name": "unregister_agent", "method": "POST", "path": "/agents/{agent_id}/unregister", "kind": "write"},
    ]

    if compass_gate_enabled():
        endpoints.extend(
            [
                {"path": "/compass/status", "method": "GET", "description": "Compass gate status for run/agent"},
                {"path": "/compass/instrument", "method": "GET", "description": "Compass instrument metadata for UI"},
                {"path": "/compass/submit", "method": "POST", "description": "Submit compass instrument answers"},
                {"path": "/compass/history", "method": "GET", "description": "List recent compass submissions"},
                {"path": "/compass/review", "method": "GET", "description": "Reviewer aggregation for run compass results"},
            ]
        )
        capabilities.append("compass_gate")
        actions.extend(
            [
                {"action_name": "compass_status", "method": "GET", "path": "/compass/status", "kind": "read"},
                {"action_name": "compass_instrument", "method": "GET", "path": "/compass/instrument", "kind": "read"},
                {"action_name": "compass_review", "method": "GET", "path": "/compass/review", "kind": "read"},
                {"action_name": "compass_submit", "method": "POST", "path": "/compass/submit", "kind": "write"},
            ]
        )

    return {
        "contract_version": "v1.0.0",
        "environment_name": _environment_name(),
        "environment_version": settings.VERSION,
        "event_schema_version": "v1",
        "journal_contract_version": "1.0",
        "description": "Moltbook social environment with platform compatibility endpoints",
        "tags": ["social", "community", "reference"],
        "endpoints": endpoints,
        "capabilities": capabilities,
        "actions": actions,
    }


@router.post("/auth/register", response_model=AuthRegisterResponse, status_code=201)
def register_agent(payload: AuthRegisterRequest, db: Session = Depends(get_db)) -> AuthRegisterResponse:
    result = _register_agent_payload(payload, db)
    try:
        db.commit()
        db.refresh(result["agent"])
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"failed to register agent: {exc}")

    agent = result["agent"]
    return AuthRegisterResponse(
        agent_id=agent.id,
        name=agent.name,
        api_token=agent.api_key,
        message="agent registered",
    )


@router.post("/auth/register/batch", response_model=AuthRegisterBatchResponse, status_code=201)
def register_agents_batch(
    payload: AuthRegisterBatchRequest,
    db: Session = Depends(get_db),
) -> AuthRegisterBatchResponse:
    if not payload.agents:
        raise HTTPException(status_code=400, detail="agents is required")

    seen_ids: set[str] = set()
    duplicates: List[str] = []
    registered_agents: List[Agent] = []
    for item in payload.agents:
        if item.agent_id in seen_ids:
            duplicates.append(item.agent_id)
            continue
        seen_ids.add(item.agent_id)
        result = _register_agent_payload(item, db)
        registered_agents.append(result["agent"])

    if duplicates:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"duplicate agent ids in batch: {', '.join(duplicates)}",
        )

    try:
        db.commit()
        for agent in registered_agents:
            db.refresh(agent)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"failed to register agent batch: {exc}")

    return AuthRegisterBatchResponse(
        registered_count=len(registered_agents),
        results=[
            AuthRegisterResponse(
                agent_id=agent.id,
                name=agent.name,
                api_token=agent.api_key,
                message="agent registered",
            )
            for agent in registered_agents
        ],
    )


def _register_agent_payload(payload: AuthRegisterRequest, db: Session) -> Dict[str, Any]:
    preferred_name = (payload.name or payload.agent_id).strip()
    if not preferred_name:
        raise HTTPException(status_code=400, detail="name is required")

    agent = db.query(Agent).filter(Agent.id == payload.agent_id).first()
    token = generate_api_key()
    verification_code = generate_verification_code()

    if agent is None:
        resolved_name = _resolve_unique_agent_name(db, preferred_name, payload.agent_id)
        agent = Agent(
            id=payload.agent_id,
            name=resolved_name,
            description=payload.description,
            api_key=token,
            verification_code=verification_code,
            is_claimed=True,
            is_active=True,
        )
        db.add(agent)
    else:
        agent.name = _resolve_unique_agent_name(db, preferred_name, payload.agent_id)
        agent.description = payload.description
        agent.api_key = token
        agent.verification_code = verification_code
        agent.is_active = True

    _ensure_default_general_submolt(db, agent)
    return {"agent": agent}


@router.post("/agents/{agent_id}/unregister")
def unregister_agent(agent_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is not None:
        agent.is_active = False
        db.commit()
    return {"success": True, "agent_id": agent_id}


@router.post("/agents/unregister")
def unregister_agent_by_payload(payload: UnregisterRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return unregister_agent(agent_id=payload.agent_id, db=db)


@router.get("/compass/status")
def compass_status(
    request: Request,
    run_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    resolved_run, requested_agent = _resolve_run_agent_context(
        request,
        run_id=run_id,
        agent_id=agent_id,
    )
    if not resolved_run:
        raise HTTPException(
            status_code=400,
            detail="compass status requires run_id and agent_id (query or x-run-id/x-agent-id headers)",
        )

    principal_agent_id = str(current_agent.id or "").strip()
    if not principal_agent_id:
        raise HTTPException(status_code=401, detail="compass status requires bearer token")

    subject_agent_id = str(requested_agent or principal_agent_id).strip() or principal_agent_id
    status_payload = resolve_compass_status(db, resolved_run, subject_agent_id)
    visibility_mode = str(status_payload.get("visibility_mode") or "private")
    instrument_version = str(status_payload.get("instrument_version") or "").strip() or None

    if subject_agent_id != principal_agent_id:
        if visibility_mode != "public":
            _record_compass_visibility_event(
                event_name="compass_result_view_denied",
                run_id=resolved_run,
                viewer_agent_id=principal_agent_id,
                subject_agent_id=subject_agent_id,
                visibility_mode=visibility_mode,
                instrument_version=instrument_version,
                surface="api",
                status_code=403,
                extra={"reason": "private_visibility"},
            )
            raise HTTPException(
                status_code=403,
                detail="compass status is private for requested agent",
            )

        latest_submission = _latest_compass_submission(
            db,
            run_id=resolved_run,
            agent_id=subject_agent_id,
            accepted_only=False,
        )
        _record_compass_visibility_event(
            event_name="compass_result_viewed",
            run_id=resolved_run,
            viewer_agent_id=principal_agent_id,
            subject_agent_id=subject_agent_id,
            visibility_mode=visibility_mode,
            instrument_version=instrument_version,
            surface="api",
        )
        return {
            "enabled": bool(status_payload.get("enabled")),
            "run_id": resolved_run,
            "agent_id": subject_agent_id,
            "viewer_agent_id": principal_agent_id,
            "visibility_mode": visibility_mode,
            "instrument_version": instrument_version,
            "result_summary": _public_compass_result_summary(latest_submission),
        }

    status_payload["next_action"] = "submit_compass" if status_payload.get("state") == "blocked" else None
    return status_payload


@router.get("/compass/instrument")
def compass_instrument(
    request: Request,
    run_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    resolved_run, requested_agent = _resolve_run_agent_context(
        request,
        run_id=run_id,
        agent_id=agent_id,
    )
    if not resolved_run:
        raise HTTPException(
            status_code=400,
            detail="compass instrument requires run_id and agent_id (query or x-run-id/x-agent-id headers)",
        )
    resolved_agent = _resolve_compass_agent_for_endpoint(
        endpoint="compass instrument",
        requested_agent_id=requested_agent,
        principal=current_agent,
        require_principal=True,
    )
    gate_status = evaluate_compass_gate(
        db,
        run_id=resolved_run,
        agent_id=resolved_agent,
    )
    if not gate_status.get("enabled"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "compass_not_enabled",
                "code": "compass_not_enabled",
                "message": "Compass gate is not enabled for this run/template",
            },
        )
    status_payload = gate_status.get("status") or {}
    instrument_version = _active_compass_instrument_version(gate_status)
    instrument_definition = _load_compass_instrument_or_http_error(instrument_version)

    return {
        "run_id": resolved_run,
        "agent_id": resolved_agent,
        "instrument_version": instrument_definition.get("instrument_version"),
        "visibility_mode": status_payload.get("visibility_mode"),
        "state": status_payload.get("state"),
        "instrument": instrument_metadata_for_client(instrument_definition),
    }


@router.get("/compass/history")
def compass_history(
    request: Request,
    run_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    resolved_run, requested_agent = _resolve_run_agent_context(
        request,
        run_id=run_id,
        agent_id=agent_id,
    )
    if not resolved_run:
        raise HTTPException(
            status_code=400,
            detail="compass history requires run_id and agent_id (query or x-run-id/x-agent-id headers)",
        )
    principal_agent_id = str(current_agent.id or "").strip()
    if not principal_agent_id:
        raise HTTPException(status_code=401, detail="compass history requires bearer token")

    resolved_agent = str(requested_agent or principal_agent_id).strip() or principal_agent_id
    if resolved_agent != principal_agent_id:
        status_payload = resolve_compass_status(db, resolved_run, resolved_agent)
        visibility_mode = str(status_payload.get("visibility_mode") or "private")
        instrument_version = str(status_payload.get("instrument_version") or "").strip() or None
        _record_compass_visibility_event(
            event_name="compass_result_view_denied",
            run_id=resolved_run,
            viewer_agent_id=principal_agent_id,
            subject_agent_id=resolved_agent,
            visibility_mode=visibility_mode,
            instrument_version=instrument_version,
            surface="api",
            status_code=403,
            extra={"reason": "history_restricted"},
        )
        raise HTTPException(
            status_code=403,
            detail="compass history is only available for the authenticated subject",
        )

    rows = (
        db.query(CompassSubmission)
        .filter(
            CompassSubmission.run_id == resolved_run,
            CompassSubmission.agent_id == resolved_agent,
        )
        .order_by(CompassSubmission.submitted_at.desc(), CompassSubmission.created_at.desc())
        .limit(limit)
        .all()
    )
    serialized = [
        {
            "submission_id": row.id,
            "accepted": bool(row.accepted),
            "instrument_version": row.instrument_version,
            "submitted_at": _to_utc(row.submitted_at).isoformat(),
            "score": row.score_json,
            "refusal_reason": row.refusal_reason,
        }
        for row in rows
    ]
    return {
        "run_id": resolved_run,
        "agent_id": resolved_agent,
        "count": len(serialized),
        "rows": serialized,
    }


@router.get("/compass/review")
def compass_review(
    request: Request,
    run_id: Optional[str] = Query(default=None),
    history_limit: int = Query(default=1000, ge=1, le=5000),
    event_limit: int = Query(default=100, ge=0, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    resolved_run, _ = _resolve_run_agent_context(
        request,
        run_id=run_id,
        agent_id=None,
    )
    if not resolved_run:
        raise HTTPException(
            status_code=400,
            detail="compass review requires run_id (query or x-run-id header)",
        )
    return _build_compass_review_payload(
        db,
        run_id=resolved_run,
        history_limit=history_limit,
        event_limit=event_limit,
    )


@router.post("/compass/submit")
def compass_submit(
    payload: CompassSubmitRequest,
    request: Request,
    run_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    current_agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    resolved_run, requested_agent = _resolve_run_agent_context(
        request,
        run_id=run_id,
        agent_id=agent_id,
    )
    if not resolved_run:
        raise HTTPException(
            status_code=400,
            detail="compass submit requires run_id and agent_id (query or x-run-id/x-agent-id headers)",
        )
    resolved_agent = _resolve_compass_agent_for_endpoint(
        endpoint="compass submit",
        requested_agent_id=requested_agent,
        principal=current_agent,
        require_principal=True,
    )

    gate_status = evaluate_compass_gate(
        db,
        run_id=resolved_run,
        agent_id=resolved_agent,
    )
    if not gate_status.get("enabled"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "compass_not_enabled",
                "code": "compass_not_enabled",
                "message": "Compass gate is not enabled for this run/template",
            },
        )

    try:
        submitted_at = _parse_iso_datetime(payload.submitted_at) if payload.submitted_at else None
    except ValueError:
        record_compass_event(
            event_name="compass_submission_rejected",
            run_id=resolved_run,
            agent_id=resolved_agent,
            status_code=422,
            details={"reason": "invalid_timestamp"},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_compass_submission",
                "code": "invalid_compass_submission",
                "message": "submitted_at must be an ISO-8601 datetime",
            },
        )
    submitted_at = submitted_at or datetime.now(timezone.utc)

    expected_instrument_version = _active_compass_instrument_version(gate_status)
    instrument_definition = _load_compass_instrument_or_http_error(expected_instrument_version)
    payload_instrument_version = (
        str(payload.instrument_version or expected_instrument_version).strip() or expected_instrument_version
    )
    if payload_instrument_version != expected_instrument_version:
        record_compass_event(
            event_name="compass_submission_rejected",
            run_id=resolved_run,
            agent_id=resolved_agent,
            status_code=422,
            details={
                "reason": "instrument_version_mismatch",
                "expected": expected_instrument_version,
                "received": payload_instrument_version,
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_compass_submission",
                "code": "invalid_compass_submission",
                "message": "instrument_version does not match active compass instrument",
                "expected": expected_instrument_version,
                "received": payload_instrument_version,
            },
        )

    refusal_reason = str(payload.refusal_reason or "").strip() or None
    if refusal_reason and payload.answers is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_compass_submission",
                "code": "invalid_compass_submission",
                "message": "provide answers or refusal_reason, not both",
            },
        )

    if refusal_reason:
        submission = CompassSubmission(
            run_id=resolved_run,
            agent_id=resolved_agent,
            instrument_version=expected_instrument_version,
            accepted=False,
            refusal_reason=refusal_reason,
            answers_json=None,
            score_json=None,
            score_total=None,
            submitted_at=submitted_at,
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)

        status_payload = resolve_compass_status(db, resolved_run, resolved_agent)
        record_compass_event(
            event_name="compass_refusal_recorded",
            run_id=resolved_run,
            agent_id=resolved_agent,
            status_code=200,
            details={
                "submission_id": submission.id,
                "instrument_version": expected_instrument_version,
            },
        )
        if status_payload.get("visibility_mode") == "public":
            _record_compass_visibility_event(
                event_name="compass_result_published",
                run_id=resolved_run,
                viewer_agent_id=None,
                subject_agent_id=resolved_agent,
                visibility_mode="public",
                instrument_version=expected_instrument_version,
                surface="api",
                extra={
                    "submission_id": submission.id,
                    "result_state": "refused",
                },
            )
        return {
            "accepted": False,
            "submission_id": submission.id,
            "instrument_version": expected_instrument_version,
            "refusal_reason": refusal_reason,
            "next_due_at": status_payload.get("due_at"),
            "grace_expires_at": status_payload.get("grace_expires_at"),
            "state": status_payload.get("state"),
            "visibility_mode": status_payload.get("visibility_mode"),
        }

    try:
        normalized_answers = validate_submission_answers(
            payload.answers,
            instrument=instrument_definition,
        )
    except InstrumentValidationError as exc:
        detail = {
            "error": "invalid_compass_submission",
            "code": "invalid_compass_submission",
            "message": str(exc),
        }
        if exc.details:
            detail.update(exc.details)
        record_compass_event(
            event_name="compass_submission_rejected",
            run_id=resolved_run,
            agent_id=resolved_agent,
            status_code=422,
            details={"reason": "invalid_answers"},
        )
        raise HTTPException(status_code=422, detail=detail) from exc

    score_payload = score_submission(
        normalized_answers,
        instrument=instrument_definition,
    )
    submission = CompassSubmission(
        run_id=resolved_run,
        agent_id=resolved_agent,
        instrument_version=expected_instrument_version,
        accepted=True,
        refusal_reason=None,
        answers_json=normalized_answers,
        score_json=score_payload,
        score_total=int(round(float(score_payload.get("total_score") or 0))),
        submitted_at=submitted_at,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    status_payload = resolve_compass_status(db, resolved_run, resolved_agent)
    record_compass_event(
        event_name="compass_submission_accepted",
        run_id=resolved_run,
        agent_id=resolved_agent,
        status_code=200,
        details={
            "submission_id": submission.id,
            "instrument_version": expected_instrument_version,
            "next_due_at": status_payload.get("due_at"),
        },
    )
    if status_payload.get("visibility_mode") == "public":
        _record_compass_visibility_event(
            event_name="compass_result_published",
            run_id=resolved_run,
            viewer_agent_id=None,
            subject_agent_id=resolved_agent,
            visibility_mode="public",
            instrument_version=expected_instrument_version,
            surface="api",
            extra={
                "submission_id": submission.id,
                "result_state": "submitted",
            },
        )
    return {
        "accepted": True,
        "submission_id": submission.id,
        "instrument_version": expected_instrument_version,
        "score": score_payload,
        "next_due_at": status_payload.get("due_at"),
        "grace_expires_at": status_payload.get("grace_expires_at"),
        "visibility_mode": status_payload.get("visibility_mode"),
        "state": status_payload.get("state"),
    }


@router.post("/run/init")
def run_init(payload: RunInitRequest) -> Dict[str, Any]:
    try:
        context = activate_run_context(payload.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    db_gen = runtime_get_db()
    db = next(db_gen)
    try:
        existing = db.query(RunContextState).filter(RunContextState.run_id == payload.run_id).first()
        assignment_payload = payload.assignment_map
        if assignment_payload is None and payload.assignment is not None:
            assignment_payload = payload.assignment
        if existing:
            if payload.environment_id:
                existing.environment_id = payload.environment_id
            if payload.params is not None:
                existing.params_json = payload.params
            if assignment_payload is not None:
                existing.assignment_json = assignment_payload
            db.commit()
            db.refresh(existing)
            return {
                "status": "ok",
                "context": context,
                "environment_id": existing.environment_id,
                "idempotent": True,
            }

        state = RunContextState(
            run_id=payload.run_id,
            environment_id=payload.environment_id,
            params_json=payload.params,
            assignment_json=assignment_payload,
        )
        db.add(state)
        db.commit()
        db.refresh(state)

        return {
            "status": "ok",
            "context": context,
            "environment_id": state.environment_id,
            "idempotent": False,
        }
    finally:
        db_gen.close()


@router.post("/run/reset")
def run_reset(payload: Optional[RunResetRequest] = None) -> Dict[str, Any]:
    context = deactivate_run_context()
    if payload and payload.run_id:
        try:
            context = activate_run_context(payload.run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "ok", "context": context}


@router.post("/run/stop")
def run_stop() -> Dict[str, Any]:
    return {"status": "ok", "context": deactivate_run_context()}


@router.get("/state/snapshot")
def state_snapshot(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {
        "generated_at": _utcnow_iso(),
        "context": run_context(),
        "counts": _snapshot_counts(db),
    }


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {
        "generated_at": _utcnow_iso(),
        "counts": _snapshot_counts(db),
        "journal_events": len(EVENT_JOURNAL),
    }


@router.get("/journal")
def journal(
    run_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
) -> Dict[str, Any]:
    rows = list(EVENT_JOURNAL)
    if run_id:
        rows = [row for row in rows if row.get("run_id") == run_id]
    if agent_id:
        rows = [row for row in rows if row.get("agent_id") == agent_id]
    rows = rows[-limit:]

    return {
        "run_id": run_id or "all",
        "generated_at": _utcnow_iso(),
        "count": len(rows),
        "rows": rows,
    }
