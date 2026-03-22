"""Route-level tests for run observability surfaces."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, Event as EventDB, Run as RunDB, RunBinding as RunBindingDB, get_db
from app.routes import events as events_routes
from app.routes import monitoring as monitoring_routes


def _build_client_and_session(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    monkeypatch.setattr(
        monitoring_routes.run_launcher,
        "get_run_status",
        lambda run_id: {
            "status": "running",
            "services": {
                "environment": {
                    "name": f"moltbook-backend-{run_id}",
                    "status": "running",
                    "health": "healthy",
                },
                "agent_worker": {
                    "name": f"moltbook-agents-{run_id}",
                    "status": "running",
                    "health": "healthy",
                },
            },
        },
    )

    app = FastAPI()
    app.include_router(events_routes.router)
    app.include_router(monitoring_routes.router)

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app), session_factory


def _seed_run_with_binding(session_factory) -> str:
    session = session_factory()
    try:
        run = RunDB(
            run_id="run-public-1",
            resolved_bundle_hash="sha256:bundle",
            status="running",
            institutional_mode=False,
            started_at=datetime(2026, 3, 22, 10, 0, 0, tzinfo=timezone.utc),
        )
        session.add(run)
        session.flush()
        session.add(
            RunBindingDB(
                run_id=run.run_id,
                environment_id="moltbook",
                runtime_id="openclaw",
                environment_ref="environment/moltbook",
                environment_config={
                    "environment_id": "moltbook",
                    "runtime_id": "openclaw",
                    "agent_model": "openai/gpt-5-mini",
                },
                snapshot={
                    "environment_id": "moltbook",
                    "runtime_id": "openclaw",
                },
                snapshot_hash="sha256:snapshot-public-1",
            )
        )
        session.add(
            EventDB(
                event_id="evt-public-1",
                run_id=run.run_id,
                environment_id="moltbook",
                agent_id="agent-1",
                event_type="action_attempt",
                action_name="http_post",
                outcome="success",
                payload={
                    "event_type": "action_attempt",
                    "method": "POST",
                    "path": "/api/v1/posts",
                    "model_id": "openai/gpt-5-mini",
                    "runtime_id": "openclaw",
                },
                trace_id="trace-public-1",
            )
        )
        session.commit()
        return run.run_id
    finally:
        session.close()


def test_run_events_hide_legacy_run_fields(monkeypatch) -> None:
    client, session_factory = _build_client_and_session(monkeypatch)
    run_id = _seed_run_with_binding(session_factory)

    with client:
        response = client.get(f"/api/v1/events/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["total"] == 1
    event = payload["events"][0]
    assert event["run_id"] == run_id
    assert event["environment_id"] == "moltbook"
    assert event["model_id"] == "openai/gpt-5-mini"
    assert "run_id" in event
    assert "environment_id" in event


def test_run_diagnostics_report_environment_and_runtime_without_version_rows(monkeypatch) -> None:
    client, session_factory = _build_client_and_session(monkeypatch)
    run_id = _seed_run_with_binding(session_factory)

    with client:
        response = client.get(f"/api/v1/monitor/runs/{run_id}/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["environment_id"] == "moltbook"
    assert payload["runtime_id"] == "openclaw"
    assert payload["snapshot_hash"] == "sha256:snapshot-public-1"
    assert "version" not in payload
