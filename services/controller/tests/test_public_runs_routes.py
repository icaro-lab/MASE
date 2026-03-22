"""Route-level tests for the active public runs router."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import (
    AgentActionEvent as AgentActionEventDB,
    Base,
    Run as RunDB,
    RunAgentAssignment as RunAgentAssignmentDB,
    RunBinding as RunBindingDB,
    RunEnvironmentAssignment as RunEnvironmentAssignmentDB,
)
from app.routes import runs as runs_routes


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def api_client(db_session, monkeypatch):
    session_factory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)
    app = FastAPI()
    app.include_router(runs_routes.router)

    monkeypatch.setattr(
        runs_routes.run_read_model,
        "resolve_run_service_urls",
        lambda run, db: {
            "environment": f"http://moltbook-backend-{run.run_id}:8000",
        },
    )
    monkeypatch.setattr(
        runs_routes.run_compat,
        "pause_run",
        lambda run_id, db: next(row for row in db.query(RunDB).all() if row.run_id == run_id),
    )

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[runs_routes.get_db] = _override_get_db
    with TestClient(app) as client:
        yield client


def _seed_public_run(db_session) -> str:
    run = RunDB(
        run_id="run-public-1",
        resolved_bundle_hash="sha256:bundle",
        seed=42,
        status="running",
        started_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
        experiment_policy_hash="sha256:policy",
        experiment_manifest_hash="sha256:manifest",
        experiment_assignment_hash="sha256:assignment",
        experiment_policy_json={"policy_version": "1.0"},
    )
    db_session.add(run)
    db_session.flush()

    db_session.add(
        RunBindingDB(
            run_id=run.run_id,
            environment_id="moltbook",
            runtime_id="openclaw",
            environment_ref="environment/moltbook",
            environment_config={
                "environment_id": "moltbook",
                "runtime_id": "openclaw",
                "agent_count": 2,
                "agent_model": "openai/gpt-5-mini",
                "experiment_policy": {
                    "core": {
                        "population_groups": {
                            "resident": {
                                "share": 1.0,
                                "runtime_id": "openclaw",
                                "model_id": "openai/gpt-5-mini",
                            }
                        }
                    }
                },
                "population_specs": {
                    "resident": {
                        "count": 2,
                        "model_id": "openai/gpt-5-mini",
                        "runtime_id": "openclaw",
                        "role_label": "resident",
                    }
                },
            },
            snapshot={"environment_id": "moltbook", "runtime_id": "openclaw"},
            snapshot_hash="sha256:snapshot",
        )
    )
    db_session.add(
        RunEnvironmentAssignmentDB(
            run_id=run.run_id,
            environment_id="moltbook",
            content_hash="sha256:env-package",
            assignment_source="native",
        )
    )
    db_session.add_all(
        [
            RunAgentAssignmentDB(
                run_id=run.run_id,
                runtime_agent_id="agent-1",
                runtime_id="openclaw",
                content_hash="sha256:a1",
                population_group="resident",
                role_label="resident",
                model_id="openai/gpt-5-mini",
                assignment_source="native",
            ),
            RunAgentAssignmentDB(
                run_id=run.run_id,
                runtime_agent_id="agent-2",
                runtime_id="openclaw",
                content_hash="sha256:a2",
                population_group="resident",
                role_label="resident",
                model_id="openai/gpt-5-mini",
                assignment_source="native",
            ),
        ]
    )
    db_session.commit()
    return run.run_id


def test_public_runs_list_route(api_client, db_session) -> None:
    run_id = _seed_public_run(db_session)

    response = api_client.get("/api/v1/runs")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert len(payload) == 1
    row = payload[0]
    assert row["run_id"] == run_id
    assert row["environment_id"] == "moltbook"
    assert row["runtime_id"] == "openclaw"
    assert row["agent_count"] == 2
    assert row["frontend_url"].startswith("http://localhost:")
    assert row["snapshot_hash"] == "sha256:snapshot"
    assert "run_id" in row
    assert "environment_id" in row


def test_public_runs_condition_manifest_route(api_client, db_session) -> None:
    run_id = _seed_public_run(db_session)

    response = api_client.get(f"/api/v1/runs/{run_id}/condition-manifest")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["environment_ref"] == "environment/moltbook"
    assert payload["population"]["agent_count"] == 2
    assert payload["population"]["groups"][0]["group_id"] == "resident"
    assert "population" in payload
    assert "environment_ref" in payload


def test_public_runs_cost_route(api_client, db_session) -> None:
    run_id = _seed_public_run(db_session)
    db_session.add_all(
        [
            AgentActionEventDB(
                event_id="evt-1",
                run_id=run_id,
                agent_id="agent-1",
                source="agent",
                action_category="System",
                action_type="llm_io",
                success=True,
                llm_cost_usd=0.2,
                ia_cost_usd=0.1,
            ),
            AgentActionEventDB(
                event_id="evt-2",
                run_id=run_id,
                agent_id="agent-2",
                source="agent",
                action_category="System",
                action_type="llm_io",
                success=True,
                llm_cost_usd=0.3,
                ia_cost_usd=0.0,
            ),
        ]
    )
    db_session.commit()

    response = api_client.get(f"/api/v1/runs/{run_id}/cost")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["totals"]["llm_cost_usd"] == pytest.approx(0.5)
    assert payload["totals"]["ia_cost_usd"] == pytest.approx(0.1)
    assert payload["totals"]["total_cost_usd"] == pytest.approx(0.6)
