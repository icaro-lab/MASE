"""Database models for the runtime/environment/run controller."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    create_engine,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/run_controller.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_migrations_applied = False


class RunStatus(PyEnum):
    """Status values for runs."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CANCELLED = "cancelled"


def generate_uuid() -> str:
    """Generate a new UUID string."""

    return str(uuid.uuid4())


class Run(Base):
    """Run/execution entity."""

    __tablename__ = "runs"

    run_id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    resolved_bundle_hash = Column(String(80), nullable=True, index=True)
    seed = Column(Integer, nullable=True)
    experiment_policy_json = Column(JSON, nullable=True)
    experiment_policy_hash = Column(String(80), nullable=True, index=True)
    experiment_manifest_hash = Column(String(80), nullable=True, index=True)
    experiment_assignment_hash = Column(String(80), nullable=True, index=True)
    experiment_policy_state = Column(String(20), nullable=True, index=True)
    status = Column(String(20), nullable=False, default=RunStatus.PENDING.value, index=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    binding = relationship(
        "RunBinding",
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    events = relationship(
        "Event",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_runs_status_started", "status", "started_at"),
        Index("ix_runs_started_ended", "started_at", "ended_at"),
        Index("ix_runs_experiment_hashes", "experiment_policy_hash", "experiment_manifest_hash"),
    )


class RunBinding(Base):
    """Run-owned environment/runtime snapshot."""

    __tablename__ = "run_bindings"

    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True, nullable=False)
    environment_id = Column(String(120), nullable=False, index=True)
    runtime_id = Column(String(120), nullable=True, index=True)
    environment_ref = Column(String(500), nullable=True)
    environment_config = Column(JSON, nullable=True)
    snapshot = Column(JSON, nullable=True)
    snapshot_hash = Column(String(80), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    run = relationship("Run", back_populates="binding")

    __table_args__ = (
        Index("ix_run_bindings_environment_runtime", "environment_id", "runtime_id"),
    )


class Event(Base):
    """Run-scoped event entity."""

    __tablename__ = "events"

    event_id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    environment_id = Column(String(100), nullable=False, index=True)
    agent_id = Column(String(255), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    action_name = Column(String(100), nullable=False)
    outcome = Column(String(50), nullable=False)
    payload = Column(JSON, nullable=True)
    trace_id = Column(String(100), nullable=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    run = relationship("Run", back_populates="events")

    __table_args__ = (
        Index("ix_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_events_agent_run", "agent_id", "run_id"),
        Index("ix_events_type_run", "event_type", "run_id"),
    )


class RunEnvironmentAssignment(Base):
    """Immutable environment snapshot bound to a run."""

    __tablename__ = "run_environment_assignments"

    id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    environment_id = Column(String(100), nullable=False, index=True)
    content_hash = Column(String(80), nullable=True, index=True)
    assignment_source = Column(String(20), nullable=False, default="native", index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_run_env_assignments_run_unique", "run_id", unique=True),
        Index("ix_run_env_assignments_environment", "environment_id"),
    )


class RunAgentAssignment(Base):
    """Immutable agent materialization bound to a runtime agent in a run."""

    __tablename__ = "run_agent_assignments"

    id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    runtime_agent_id = Column(String(255), nullable=False, index=True)
    runtime_id = Column(String(100), nullable=False, index=True)
    content_hash = Column(String(80), nullable=True, index=True)
    population_group = Column(String(120), nullable=True, index=True)
    role_label = Column(String(120), nullable=True, index=True)
    model_id = Column(String(255), nullable=True, index=True)
    assignment_source = Column(String(20), nullable=False, default="native", index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_run_agent_assignments_run_agent_unique", "run_id", "runtime_agent_id", unique=True),
        Index("ix_run_agent_assignments_runtime", "runtime_id"),
    )


class AgentActionEvent(Base):
    """Individual action telemetry rows."""

    __tablename__ = "agent_action_events"

    event_id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(255), nullable=True, index=True)
    source = Column(String(50), nullable=False, index=True)
    action_category = Column(String(50), nullable=False, index=True)
    action_type = Column(String(100), nullable=False, index=True)
    skill_name = Column(String(255), nullable=True, index=True)
    intent = Column(Text, nullable=True)
    parent_event_id = Column(String(36), nullable=True, index=True)
    payload = Column(JSON, nullable=True, default=dict)
    success = Column(Boolean, nullable=False, default=False, index=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    llm_tokens_input = Column(Integer, nullable=True, default=0)
    llm_tokens_output = Column(Integer, nullable=True, default=0)
    llm_cost_usd = Column(Float, nullable=True, default=0.0)
    ia_cost_usd = Column(Float, nullable=True, default=0.0)
    ia_metadata = Column(JSON, nullable=True, default=dict)

    __table_args__ = (
        Index("ix_agent_action_events_run_timestamp", "run_id", "timestamp"),
        Index("ix_agent_action_events_agent_timestamp", "agent_id", "timestamp"),
        Index("ix_agent_action_events_category_type", "action_category", "action_type"),
    )


class AgentMetrics(Base):
    """Aggregated metrics per agent per run."""

    __tablename__ = "agent_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(255), nullable=False, index=True)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False)
    total_actions = Column(Integer, nullable=False, default=0)
    successful_actions = Column(Integer, nullable=False, default=0)
    failed_actions = Column(Integer, nullable=False, default=0)
    self_actions = Column(Integer, nullable=False, default=0)
    environmental_actions = Column(Integer, nullable=False, default=0)
    system_actions = Column(Integer, nullable=False, default=0)
    institutional_actions = Column(Integer, nullable=False, default=0)
    skill_counts = Column(JSON, nullable=True, default=dict)
    llm_cost_usd = Column(Float, nullable=False, default=0.0)
    ia_cost_usd = Column(Float, nullable=False, default=0.0)
    llm_tokens_input = Column(Integer, nullable=False, default=0)
    llm_tokens_output = Column(Integer, nullable=False, default=0)
    total_duration_ms = Column(Integer, nullable=False, default=0)
    avg_duration_ms = Column(Float, nullable=True)
    max_duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_agent_metrics_run_agent_window", "run_id", "agent_id", "window_start"),
        Index("ix_agent_metrics_window", "window_start"),
    )


class RunMetrics(Base):
    """Aggregated metrics per run."""

    __tablename__ = "run_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid, nullable=False)
    run_id = Column(String(36), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, index=True, unique=True)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False)
    total_agents = Column(Integer, nullable=False, default=0)
    active_agents = Column(Integer, nullable=False, default=0)
    total_events = Column(Integer, nullable=False, default=0)
    events_by_category = Column(JSON, nullable=True, default=dict)
    events_by_source = Column(JSON, nullable=True, default=dict)
    total_llm_cost_usd = Column(Float, nullable=False, default=0.0)
    total_ia_cost_usd = Column(Float, nullable=False, default=0.0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    total_llm_tokens_input = Column(Integer, nullable=False, default=0)
    total_llm_tokens_output = Column(Integer, nullable=False, default=0)
    total_duration_ms = Column(Integer, nullable=False, default=0)
    avg_duration_ms = Column(Float, nullable=True)
    success_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_run_metrics_window", "window_start"),
    )


def get_db():
    """Generator for FastAPI dependency injection."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database schema for fresh stacks."""
    global _migrations_applied
    _migrations_applied = False
    Base.metadata.create_all(bind=engine)
