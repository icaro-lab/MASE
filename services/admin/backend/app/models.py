"""Pydantic models for the admin dashboard API."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EnvironmentInfo(BaseModel):
    """Environment information from the controller."""

    name: str
    description: str = ""
    version: str = "unknown"
    tags: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)


class EnvironmentStatus(BaseModel):
    """Environment status."""

    name: str
    status: str
    services: list[str] = Field(default_factory=list)
    urls: dict[str, str] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)


class EnvironmentListResponse(BaseModel):
    """Response with available environments."""

    environments: list[EnvironmentInfo]


class OpenRouterSettingsPayload(BaseModel):
    """Payload for setting fallback OpenRouter API key."""

    openrouter_api_key: str = Field(default="")


class OpenRouterSettingsResponse(BaseModel):
    """Visibility-safe OpenRouter key status for the UI."""

    openrouter_api_key_configured: bool
    openrouter_api_key_masked: Optional[str] = None
    source: str


class ServiceHealth(BaseModel):
    """Individual service health status."""

    name: str
    status: str
    response_time_ms: Optional[float] = None
    error: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class SystemHealth(BaseModel):
    """Overall system health status."""

    status: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    services: list[ServiceHealth]
    active_runs: int
