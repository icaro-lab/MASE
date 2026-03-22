"""Runtime and environment discovery routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.catalog import (
    discover_environments,
    discover_runtimes,
    get_environment,
    get_runtime,
    validate_environment,
)


router = APIRouter(prefix="/api/v1", tags=["runtime-environment"])


class RuntimeManifestResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    required_population_files: list[str] = Field(default_factory=list)
    baseline_tool_families: list[str] = Field(default_factory=list)
    service: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class PopulationResponse(BaseModel):
    path: str
    runtime_id: str | None = None
    display_name_prefix: str | None = None
    default_count: int | None = None
    default_model: str | None = None
    tools_manifest: str | None = None


class RunHookResponse(BaseModel):
    id: str
    trigger: str = "on_run_start"
    script: str
    background: bool = True


class EnvironmentManifestResponse(BaseModel):
    id: str
    name: str
    runtime: str
    description: str = ""
    world_base: str | None = None
    params_schema: dict[str, Any] = Field(default_factory=dict)
    runtime_defaults: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    backend_contract: dict[str, Any] = Field(default_factory=dict)
    environment_skills: list[str] = Field(default_factory=list)
    populations: dict[str, PopulationResponse] = Field(default_factory=dict)
    run_hooks: list[RunHookResponse] = Field(default_factory=list)
    data_sources: dict[str, Any] = Field(default_factory=dict)
    analysis_exports: list[str] = Field(default_factory=list)
    launch: dict[str, Any] = Field(default_factory=dict)


class EnvironmentValidationResponse(BaseModel):
    environment_id: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _strip_manifest_internal_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


@router.get("/runtimes", response_model=list[RuntimeManifestResponse])
def list_runtimes() -> list[RuntimeManifestResponse]:
    manifests = [_strip_manifest_internal_fields(item) for item in discover_runtimes()]
    return [RuntimeManifestResponse.model_validate(item) for item in manifests]


@router.get("/runtimes/{runtime_id}", response_model=RuntimeManifestResponse)
def get_runtime_manifest(runtime_id: str) -> RuntimeManifestResponse:
    manifest = get_runtime(runtime_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Runtime not found: {runtime_id}")
    return RuntimeManifestResponse.model_validate(_strip_manifest_internal_fields(manifest))


@router.get("/environments", response_model=list[EnvironmentManifestResponse])
def list_environments() -> list[EnvironmentManifestResponse]:
    manifests = [_strip_manifest_internal_fields(item) for item in discover_environments()]
    return [EnvironmentManifestResponse.model_validate(item) for item in manifests]


@router.get("/environments/{environment_id}", response_model=EnvironmentManifestResponse)
def get_environment_manifest(environment_id: str) -> EnvironmentManifestResponse:
    manifest = get_environment(environment_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Environment not found: {environment_id}")
    return EnvironmentManifestResponse.model_validate(_strip_manifest_internal_fields(manifest))


@router.post(
    "/environments/{environment_id}/validate",
    response_model=EnvironmentValidationResponse,
)
def validate_environment_manifest(environment_id: str) -> EnvironmentValidationResponse:
    return EnvironmentValidationResponse.model_validate(validate_environment(environment_id))
