"""Unit tests for reserved per-run frontend ports."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, Run as RunDB, RunBinding as RunBindingDB
from app.run_frontend_ports import apply_frontend_port, reserve_frontend_port
from app.run_launcher import RunLauncher
from app.run_read_model import build_restart_launch_config


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


@pytest.mark.unit
def test_reserve_frontend_port_skips_reserved_binding_ports(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_session.add_all(
        [
            RunBindingDB(
                run_id="run-a",
                environment_id="hello-world",
                environment_config={"launch": {"frontend_port": 18100}},
                snapshot={"launch": {"frontend_port": 18100}},
            ),
            RunBindingDB(
                run_id="run-b",
                environment_id="hello-world",
                environment_config={"launch": {"frontend_port": 18101}},
                snapshot={"launch": {"frontend_port": 18101}},
            ),
        ]
    )
    db_session.commit()

    monkeypatch.setattr(RunLauncher, "get_environment_frontend_port", classmethod(lambda cls, run_id, namespace=None: 18100))

    reserved = reserve_frontend_port(
        db_session,
        run_id="run-c",
        environment_has_frontend=True,
    )

    assert reserved == 18102


@pytest.mark.unit
def test_apply_frontend_port_persists_launch_metadata() -> None:
    environment_config = {"launch": {"environment_id": "hello-world"}}
    snapshot = {"launch": {"environment_id": "hello-world"}}

    apply_frontend_port(
        run_id="run-123",
        environment_config=environment_config,
        snapshot=snapshot,
        frontend_port=18234,
    )

    assert environment_config["launch"]["frontend_port"] == 18234
    assert snapshot["launch"]["frontend_port"] == 18234
    assert environment_config["launch"]["frontend_url"] == "http://localhost:18234"
    assert snapshot["launch"]["frontend_url"] == "http://localhost:18234"


@pytest.mark.unit
def test_build_restart_launch_config_uses_reserved_frontend_port(db_session) -> None:
    run = RunDB(
        run_id="run-front",
        resolved_bundle_hash="sha256:bundle",
        status="completed",
        started_at=datetime(2026, 3, 30, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        RunBindingDB(
            run_id=run.run_id,
            environment_id="hello-world",
            runtime_id="openclaw",
            environment_ref="environment/hello-world",
            environment_config={
                "environment_id": "hello-world",
                "runtime_id": "openclaw",
                "launch": {"frontend_port": 18234},
            },
            snapshot={"environment_id": "hello-world", "runtime_id": "openclaw", "launch": {"frontend_port": 18234}},
        )
    )
    db_session.commit()

    config = build_restart_launch_config(run, db_session)

    assert config.frontend_port == 18234
