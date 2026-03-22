"""Pydantic models for the runtime/environment/run controller."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    """Active run lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunBase(BaseModel):
    """Base payload for run launch inputs."""

    institutional_mode: bool = Field(
        default=False,
        description="Whether institutional mode is enabled for this run.",
    )
    seed: Optional[int] = Field(default=None, description="Random seed for reproducibility.")
    api_key: Optional[str] = Field(
        default=None,
        description="Provider API key used by runtime agents.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "institutional_mode": False,
                "seed": 42,
                "api_key": "sk-or-v1-...",
            }
        }
    )


class RunCreate(RunBase):
    """Create request for a run."""


class Run(RunBase):
    """Full run read model used by the active public API."""

    run_id: str = Field(..., description="Unique run identifier.")
    resolved_bundle_hash: Optional[str] = Field(
        default=None,
        description="Resolved launch snapshot hash for this run.",
    )
    environment_url: Optional[str] = Field(
        default=None,
        description="Internal environment backend URL for this run.",
    )
    frontend_url: Optional[str] = Field(
        default=None,
        description="Browser URL for environment frontend preview.",
    )
    status: RunStatus = Field(default=RunStatus.PENDING, description="Run status.")
    started_at: Optional[datetime] = Field(default=None, description="Start timestamp.")
    ended_at: Optional[datetime] = Field(default=None, description="End timestamp.")
    runtime_limit_minutes: Optional[int] = Field(
        default=None,
        description="Configured run time limit in minutes.",
    )
    agent_count: Optional[int] = Field(
        default=None,
        description="Number of agents configured for this run.",
    )
    initialized_agents: Optional[List[str]] = Field(
        default=None,
        description="List of initialized agent IDs.",
    )
    experiment_policy_hash: Optional[str] = Field(
        default=None,
        description="Canonical policy hash pinned to this run.",
    )
    experiment_manifest_hash: Optional[str] = Field(
        default=None,
        description="Pinned environment manifest hash used for policy validation.",
    )
    experiment_assignment_hash: Optional[str] = Field(
        default=None,
        description="Deterministic assignment hash for role/model mapping.",
    )
    experiment_policy_state: Optional[str] = Field(
        default=None,
        description="Policy lifecycle state: validated | applied | rejected.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "run_id": "run_def456",
                "resolved_bundle_hash": "sha256:a1b2c3...",
                "environment_url": "http://environment-backend-run_def456:8000",
                "frontend_url": "http://localhost:18432",
                "status": "running",
                "institutional_mode": False,
                "seed": 42,
                "started_at": "2026-02-08T10:05:00Z",
                "ended_at": None,
                "runtime_limit_minutes": 5,
                "agent_count": 10,
                "initialized_agents": ["agent-1", "agent-2"],
                "experiment_policy_hash": "sha256:aabbcc...",
                "experiment_manifest_hash": "sha256:ddeeff...",
                "experiment_assignment_hash": "sha256:112233...",
                "experiment_policy_state": "applied",
            }
        }
    )


class RunStackRestartResponse(BaseModel):
    """Response payload for on-demand terminal run stack relaunch."""

    run: Run = Field(..., description="Run record after stack relaunch.")
    service_urls: Dict[str, str] = Field(
        default_factory=dict,
        description="Resolved per-run service URLs after relaunch.",
    )
    stack_status: str = Field(
        ...,
        description="Stack relaunch outcome: relaunched | already_running.",
    )
    restarted_at: datetime = Field(..., description="UTC timestamp of relaunch response.")
