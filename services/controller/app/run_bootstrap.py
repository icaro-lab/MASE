"""Neutral run bootstrap helpers for the runtime/environment/run controller."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.catalog import load_population_materialization
from app.database import (
    Run as RunDB,
    RunAgentAssignment as RunAgentAssignmentDB,
    RunEnvironmentAssignment as RunEnvironmentAssignmentDB,
)
from app.heartbeat_contract import resolve_scheduler_heartbeat_timeout
from app.package_hashes import (
    PACKAGE_KIND_RUNTIME,
    PACKAGE_KIND_ENVIRONMENT,
    normalize_environment_ref,
    resolve_runtime_heartbeat_interval,
    resolve_package_hash,
)


logger = logging.getLogger(__name__)


def _hash_canonical_json(payload: Dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        return int(float(str(value).strip()))
    except Exception:
        return default


def normalize_environment_config_for_run(
    *,
    environment_ref: str,
    environment_config: Optional[Dict[str, Any]],
    db: Optional[Session] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Normalize environment config for the public environment-run path."""
    _ = environment_ref, db
    if environment_config is not None and not isinstance(environment_config, dict):
        raise HTTPException(status_code=400, detail="environment_config must be an object when provided")
    if not isinstance(environment_config, dict):
        return environment_config, None, None, None
    return deepcopy(environment_config), None, None, None


def _derive_assignment_rng_seed(run_seed: Optional[int], assignment_plan_hash: str) -> int:
    seed_payload = {
        "run_seed": int(run_seed) if isinstance(run_seed, int) else run_seed,
        "assignment_plan_hash": assignment_plan_hash,
        "algorithm": "population_specs_v1",
    }
    seed_hash = _hash_canonical_json(seed_payload)
    digest = seed_hash.split(":", 1)[1]
    return int(digest[:16], 16)


def _resolve_population_specs_for_assignment(env_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_population_specs = env_config.get("population_specs")
    if not isinstance(raw_population_specs, dict) or not raw_population_specs:
        raise ValueError("environment_config.population_specs must be a non-empty object")

    default_runtime_id = _normalize_optional_text(env_config.get("runtime_id")) or "openclaw"
    default_model_id = _normalize_optional_text(env_config.get("agent_model")) or "openai/gpt-5-mini"

    population_specs: Dict[str, Dict[str, Any]] = {}
    for raw_population_id in sorted(raw_population_specs.keys(), key=lambda item: str(item)):
        population_id = _normalize_optional_text(raw_population_id)
        if not population_id:
            raise ValueError("population_specs contains an empty population id")
        spec = raw_population_specs.get(raw_population_id)
        if not isinstance(spec, dict):
            raise ValueError(f"population_specs.{population_id} must be an object")

        count = _coerce_int(spec.get("count"), default=0)
        if count <= 0:
            continue
        runtime_id = _normalize_optional_text(spec.get("runtime_id")) or default_runtime_id
        model_id = _normalize_optional_text(spec.get("model_id")) or default_model_id
        role_label = _normalize_optional_text(spec.get("role_label")) or population_id
        population_specs[population_id] = {
            "count": count,
            "runtime_id": runtime_id,
            "model_id": model_id,
            "role_label": role_label,
        }

    if not population_specs:
        raise ValueError("Resolved population count is zero")
    return population_specs


def _runtime_agent_sort_key(agent_id: str) -> Tuple[int, Any]:
    match = re.match(r"^agent-(\d+)$", str(agent_id))
    if match:
        return 0, int(match.group(1))
    return 1, str(agent_id)


def _build_population_assignment_map(
    *,
    population_specs: Dict[str, Dict[str, Any]],
    run_seed: Optional[int],
    assignment_plan_hash: str,
) -> Dict[str, Dict[str, Any]]:
    if not population_specs:
        return {}

    assignment_rows: List[Dict[str, Any]] = []
    for population_id in sorted(population_specs.keys()):
        population_payload = population_specs[population_id]
        for _ in range(max(int(population_payload.get("count") or 0), 0)):
            assignment_rows.append(
                {
                    "population_group": population_id,
                    "role_label": _normalize_optional_text(population_payload.get("role_label")),
                    "runtime_id": _normalize_optional_text(population_payload.get("runtime_id")),
                    "content_hash": _normalize_optional_text(population_payload.get("content_hash")),
                    "model_id": _normalize_optional_text(population_payload.get("model_id")),
                }
            )

    if not assignment_rows:
        return {}

    rng = random.Random(_derive_assignment_rng_seed(run_seed, assignment_plan_hash))
    rng.shuffle(assignment_rows)

    assignment_map: Dict[str, Dict[str, Any]] = {}
    for idx, row in enumerate(assignment_rows):
        runtime_agent_id = f"agent-{idx + 1}"
        role_label = _normalize_optional_text(row.get("role_label"))
        assignment_map[runtime_agent_id] = {
            "runtime_id": row.get("runtime_id"),
            "content_hash": row.get("content_hash"),
            "population_group": row.get("population_group"),
            "role_label": role_label,
            "role": role_label,
            "model_id": row.get("model_id"),
        }

    return {
        agent_id: assignment_map[agent_id]
        for agent_id in sorted(assignment_map.keys(), key=_runtime_agent_sort_key)
    }


def persist_run_assignments(
    db: Session,
    *,
    run: RunDB,
    environment_ref: str,
    environment_config: Dict[str, Any],
) -> Dict[str, Any]:
    env_config = environment_config if isinstance(environment_config, dict) else {}
    environment_id = normalize_environment_ref(environment_ref)
    if not environment_id:
        raise ValueError("Run is missing base environment reference")

    env_content_hash = resolve_package_hash(
        db,
        kind=PACKAGE_KIND_ENVIRONMENT,
        package_id=environment_id,
    )

    default_runtime_id = str(env_config.get("runtime_id") or "openclaw")
    requested_runtime_source = _normalize_optional_text(env_config.get("runtime_source")) or "snapshot"
    use_agent_draft = requested_runtime_source == "draft"
    if use_agent_draft:
        runtime_content_hash = None
    else:
        runtime_content_hash = resolve_package_hash(
            db,
            kind=PACKAGE_KIND_RUNTIME,
            package_id=default_runtime_id,
        )
    default_model_id = _normalize_optional_text(env_config.get("agent_model")) or "openai/gpt-5-mini"
    population_specs = _resolve_population_specs_for_assignment(env_config)
    agent_count = sum(int(spec.get("count") or 0) for spec in population_specs.values())
    for population_id in sorted(population_specs.keys()):
        population_payload = population_specs[population_id]
        runtime_id = str(population_payload.get("runtime_id") or "").strip()
        if not runtime_id:
            raise ValueError(f"population_specs.{population_id}.runtime_id is required")
        if use_agent_draft:
            population_payload["content_hash"] = None
        else:
            population_payload["content_hash"] = resolve_package_hash(
                db,
                kind=PACKAGE_KIND_RUNTIME,
                package_id=runtime_id,
            )

    assignment_plan_payload = {
        "algorithm": "population_specs_v1",
        "run_seed": int(run.seed) if isinstance(run.seed, int) else run.seed,
        "environment_id": environment_id,
        "environment_content_hash": env_content_hash,
        "population_specs": population_specs,
    }
    assignment_plan_hash = _hash_canonical_json(assignment_plan_payload)
    assignment_map = _build_population_assignment_map(
        population_specs=population_specs,
        run_seed=run.seed,
        assignment_plan_hash=assignment_plan_hash,
    )

    db.add(
        RunEnvironmentAssignmentDB(
            run_id=run.run_id,
            environment_id=environment_id,
            content_hash=env_content_hash,
            assignment_source="native",
        )
    )
    for idx in range(agent_count):
        runtime_agent_id = f"agent-{idx + 1}"
        agent_assignment = assignment_map.get(runtime_agent_id) or {}
        agent_runtime_id = _normalize_optional_text(agent_assignment.get("runtime_id")) or default_runtime_id
        agent_content = _normalize_optional_text(agent_assignment.get("content_hash")) or runtime_content_hash
        role_label = _normalize_optional_text(agent_assignment.get("role_label"))
        population_group = _normalize_optional_text(agent_assignment.get("population_group"))
        model_id = _normalize_optional_text(agent_assignment.get("model_id")) or default_model_id
        db.add(
            RunAgentAssignmentDB(
                run_id=run.run_id,
                runtime_agent_id=runtime_agent_id,
                runtime_id=agent_runtime_id,
                content_hash=agent_content,
                population_group=population_group,
                role_label=role_label,
                model_id=model_id,
                assignment_source="native",
            )
        )
        assignment_map[runtime_agent_id] = {
            "runtime_id": agent_runtime_id,
            "content_hash": agent_content,
            "population_group": population_group,
            "role_label": role_label,
            "role": role_label,
            "model_id": model_id,
        }
    db.commit()

    unique_model_ids = sorted(
        {
            str(row.get("model_id")).strip()
            for row in assignment_map.values()
            if str(row.get("model_id") or "").strip()
        }
    )
    return {
        "environment_id": environment_id,
        "environment_content_hash": env_content_hash,
        "runtime_id": default_runtime_id,
        "runtime_content_hash": runtime_content_hash,
        "agent_model": default_model_id,
        "agent_count": agent_count,
        "population_specs": population_specs,
        "assignment_plan_hash": assignment_plan_hash,
        "assignment_map": assignment_map,
        "unique_model_ids": unique_model_ids,
    }


def _build_run_init_payload(
    *,
    run_id: str,
    environment_id: Optional[str],
    environment_params: Optional[Dict[str, Any]],
    assignment_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "environment_id": environment_id,
        "params": dict(environment_params or {}),
        "assignment": {
            "agent_count": assignment_snapshot.get("agent_count"),
            "runtime_id": assignment_snapshot.get("runtime_id"),
            "runtime_content_hash": assignment_snapshot.get("runtime_content_hash"),
            "agent_model": assignment_snapshot.get("agent_model"),
            "assignment_plan_hash": assignment_snapshot.get("assignment_plan_hash"),
            "population_specs": assignment_snapshot.get("population_specs"),
        },
        "assignment_map": assignment_snapshot.get("assignment_map"),
    }


async def init_environment_run_context(
    *,
    environment_url: str,
    run_id: str,
    environment_id: Optional[str],
    environment_params: Optional[Dict[str, Any]],
    assignment_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    payload = _build_run_init_payload(
        run_id=run_id,
        environment_id=environment_id,
        environment_params=environment_params,
        assignment_snapshot=assignment_snapshot,
    )
    url = f"{environment_url}/run/init"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
        body_text = response.text or ""
        body_json: Dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body_json = parsed
        except Exception:
            body_json = {}
        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": body_text.strip()[:2000],
                "payload": body_json,
            }
        return {
            "success": True,
            "status_code": response.status_code,
            "payload": body_json,
        }
    except Exception as exc:
        return {
            "success": False,
            "status_code": None,
            "error": str(exc),
            "payload": {},
        }


def load_environment_population_materializations(
    env_config: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    environment_id = _normalize_optional_text(env_config.get("environment_id"))
    if not environment_id:
        return {}
    population_specs = env_config.get("population_specs")
    if not isinstance(population_specs, dict) or not population_specs:
        return {}

    materializations: Dict[str, Dict[str, str]] = {}
    for raw_population_id in sorted(population_specs.keys(), key=lambda item: str(item)):
        population_id = _normalize_optional_text(raw_population_id)
        if not population_id:
            continue
        try:
            materializations[population_id] = load_population_materialization(
                environment_id,
                population_id,
            )
        except Exception as exc:
            raise ValueError(
                f"Failed to load environment population files for {environment_id}/{population_id}: {exc}"
            ) from exc
    return materializations


@dataclass(frozen=True)
class AgentInitSpec:
    agent_id: str
    agent_name: str
    runtime_id: str
    runtime_content_hash: Optional[str]
    model_id: str
    population_group: Optional[str] = None
    role_label: Optional[str] = None
    agents: Optional[str] = None
    identity: Optional[str] = None
    soul: Optional[str] = None
    tools: Optional[str] = None
    bootstrap: Optional[str] = None
    user: Optional[str] = None
    heartbeat: Optional[str] = None


@dataclass(frozen=True)
class AgentInitResult:
    spec: AgentInitSpec
    api_token: Optional[str] = None


async def init_agent_filesystem(
    agent_launcher_url: str,
    agent_id: str,
    agent_name: str,
    runtime_id: str,
    run_id: str,
    runtime_content_hash: Optional[str] = None,
    environment_url: Optional[str] = None,
    environment_name: Optional[str] = None,
    agents: Optional[str] = None,
    identity: Optional[str] = None,
    soul: Optional[str] = None,
    tools: Optional[str] = None,
    bootstrap: Optional[str] = None,
    user: Optional[str] = None,
    heartbeat: Optional[str] = None,
    timeout_seconds: float = 600.0,
) -> dict:
    url = f"{agent_launcher_url}/agents"
    payload = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "run_id": run_id,
        "runtime_id": runtime_id,
        "runtime_content_hash": runtime_content_hash,
        "environment_url": environment_url,
        "environment_name": environment_name,
        "agents": agents,
        "identity": identity,
        "soul": soul,
        "tools": tools,
        "bootstrap": bootstrap,
        "user": user,
        "heartbeat": heartbeat,
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.ReadTimeout as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Agent filesystem initialization timed out for {agent_id} after {int(timeout_seconds)}s",
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or exc.response.reason_phrase or str(exc)
            raise HTTPException(
                status_code=502,
                detail=f"Agent filesystem initialization failed for {agent_id}: {exc.response.status_code} {detail}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Agent filesystem initialization request failed for {agent_id}: {exc}",
            ) from exc


async def init_agents_filesystem_batch(
    *,
    agent_launcher_url: str,
    specs: List[AgentInitSpec],
    run_id: str,
    environment_url: Optional[str] = None,
    environment_name: Optional[str] = None,
    concurrency: int = 10,
    timeout_seconds: float = 600.0,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    if not specs:
        return {}, []

    url = f"{agent_launcher_url}/agents/batch"
    payload = {
        "concurrency": max(1, concurrency),
        "agents": [
            {
                "agent_id": spec.agent_id,
                "agent_name": spec.agent_name,
                "run_id": run_id,
                "runtime_id": spec.runtime_id,
                "runtime_content_hash": spec.runtime_content_hash,
                "environment_url": environment_url,
                "environment_name": environment_name,
                "agents": spec.agents,
                "identity": spec.identity,
                "soul": spec.soul,
                "tools": spec.tools,
                "bootstrap": spec.bootstrap,
                "user": spec.user,
                "heartbeat": spec.heartbeat,
            }
            for spec in specs
        ],
    }
    requested_ids = [spec.agent_id for spec in specs]
    successes: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        except httpx.ReadTimeout as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Batch agent filesystem initialization timed out for {len(specs)} agents after {int(timeout_seconds)}s",
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or exc.response.reason_phrase or str(exc)
            raise HTTPException(
                status_code=502,
                detail=f"Batch agent filesystem initialization failed: {exc.response.status_code} {detail}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Batch agent filesystem initialization request failed: {exc}",
            ) from exc

    raw_results = result.get("results")
    if not isinstance(raw_results, list):
        raise HTTPException(
            status_code=502,
            detail="Batch agent filesystem initialization returned an invalid response payload",
        )

    seen_ids: set[str] = set()
    for raw_item in raw_results:
        if not isinstance(raw_item, dict):
            errors.append("unknown: invalid batch result item")
            continue
        agent_id = str(raw_item.get("agent_id") or "").strip()
        if not agent_id:
            errors.append("unknown: missing agent_id in batch result")
            continue
        seen_ids.add(agent_id)
        if bool(raw_item.get("created")):
            successes[agent_id] = raw_item
            continue
        error_detail = str(raw_item.get("error") or "unknown initialization error")
        errors.append(f"{agent_id}: {error_detail}")

    missing_ids = [agent_id for agent_id in requested_ids if agent_id not in seen_ids]
    for agent_id in missing_ids:
        errors.append(f"{agent_id}: missing batch initialization result")
    return successes, errors


def _validate_materialized_agent_result(
    *,
    spec: AgentInitSpec,
    init_result: Dict[str, Any],
    environment_name: str,
) -> None:
    runtime_resolution = init_result.get("runtime_resolution") if isinstance(init_result, dict) else None
    resolved_agent_hash = str((runtime_resolution or {}).get("resolved_content_hash") or "").strip()
    if spec.runtime_content_hash and resolved_agent_hash != str(spec.runtime_content_hash):
        raise HTTPException(
            status_code=500,
            detail=(
                "Agent launcher materialized an unexpected runtime hash "
                f"for {spec.agent_id}: expected={spec.runtime_content_hash}, resolved={resolved_agent_hash or 'unknown'}"
            ),
        )

    bootstrap = init_result.get("environment_bootstrap")
    if environment_name:
        if not bootstrap:
            raise HTTPException(
                status_code=500,
                detail=f"Agent launcher did not return environment bootstrap result for {spec.agent_id} ({environment_name})",
            )
        if not bootstrap.get("success"):
            bootstrap_error = str(bootstrap.get("error") or "unknown")
            raise HTTPException(
                status_code=500,
                detail=f"Agent environment skill bootstrap failed for {spec.agent_id} ({environment_name}): {bootstrap_error}",
            )


async def register_agent_with_environment(
    environment_url: str,
    agent_id: str,
    environment_name: str,
    *,
    agent_name: Optional[str] = None,
    description: Optional[str] = None,
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    url = f"{environment_url}/auth/register"
    payload = {
        "agent_id": agent_id,
        "name": str(agent_name or agent_id),
        "description": str(description or f"Agent registered for {environment_name}"),
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
        except httpx.ReadTimeout as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Environment registration timed out for {agent_id} after {int(timeout_seconds)}s",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Environment registration request failed for {agent_id}: {exc}",
            ) from exc
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise HTTPException(
                status_code=502,
                detail=f"Environment registration failed for {agent_id} at {environment_url}: {response.status_code} {detail}",
            )
        register_result = response.json()

    missing_fields = [field for field in ("agent_id", "api_token") if not register_result.get(field)]
    if missing_fields:
        raise HTTPException(
            status_code=502,
            detail=f"Environment registration response missing required fields for {agent_id}: {', '.join(missing_fields)}",
        )
    return register_result


async def register_agents_with_environment_batch(
    *,
    environment_url: str,
    specs: List[AgentInitSpec],
    environment_name: str,
    timeout_seconds: float = 120.0,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    if not specs:
        return {}, []

    url = f"{environment_url}/auth/register/batch"
    payload = {
        "agents": [
            {
                "agent_id": spec.agent_id,
                "name": str(spec.agent_name or spec.agent_id),
                "description": str(f"Agent registered for {environment_name}"),
            }
            for spec in specs
        ]
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            response = await client.post(url, json=payload)
        except httpx.ReadTimeout as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Environment batch registration timed out for {len(specs)} agents after {int(timeout_seconds)}s",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Environment batch registration request failed: {exc}",
            ) from exc
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise HTTPException(
                status_code=502,
                detail=f"Environment batch registration failed at {environment_url}: {response.status_code} {detail}",
            )
        register_result = response.json()

    raw_results = register_result.get("results")
    if not isinstance(raw_results, list):
        raise HTTPException(
            status_code=502,
            detail="Environment batch registration returned an invalid response payload",
        )

    successes: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    seen_ids: set[str] = set()
    for raw_item in raw_results:
        if not isinstance(raw_item, dict):
            errors.append("unknown: invalid batch registration result")
            continue
        agent_id = str(raw_item.get("agent_id") or "").strip()
        if not agent_id:
            errors.append("unknown: missing agent_id in registration result")
            continue
        seen_ids.add(agent_id)
        if not raw_item.get("api_token"):
            errors.append(f"{agent_id}: missing api_token in registration result")
            continue
        successes[agent_id] = raw_item

    missing_ids = [spec.agent_id for spec in specs if spec.agent_id not in seen_ids]
    for agent_id in missing_ids:
        errors.append(f"{agent_id}: missing batch registration result")
    return successes, errors


async def _initialize_agent_remote(
    *,
    spec: AgentInitSpec,
    agent_launcher_url: str,
    run_id: str,
    environment_url: str,
    environment_name: str,
    pre_register_agents: bool,
    init_timeout_seconds: float,
) -> AgentInitResult:
    init_result = await init_agent_filesystem(
        agent_launcher_url=agent_launcher_url,
        agent_id=spec.agent_id,
        agent_name=spec.agent_name,
        runtime_id=spec.runtime_id,
        run_id=run_id,
        runtime_content_hash=spec.runtime_content_hash,
        environment_url=environment_url,
        environment_name=environment_name,
        agents=spec.agents,
        identity=spec.identity,
        soul=spec.soul,
        tools=spec.tools,
        bootstrap=spec.bootstrap,
        user=spec.user,
        heartbeat=spec.heartbeat,
        timeout_seconds=init_timeout_seconds,
    )
    _validate_materialized_agent_result(
        spec=spec,
        init_result=init_result,
        environment_name=environment_name,
    )

    api_token: Optional[str] = None
    if pre_register_agents:
        register_result = await register_agent_with_environment(
            environment_url=environment_url,
            agent_id=spec.agent_id,
            environment_name=environment_name,
            agent_name=spec.agent_name,
            timeout_seconds=init_timeout_seconds,
        )
        api_token = str(register_result.get("api_token") or "").strip() or None
        if not api_token:
            raise HTTPException(
                status_code=502,
                detail=f"Environment registration missing api_token for {spec.agent_id}",
            )
    return AgentInitResult(spec=spec, api_token=api_token)


async def run_bounded_agent_initialization(
    *,
    specs: List[AgentInitSpec],
    concurrency: int,
    agent_launcher_url: str,
    run_id: str,
    environment_url: str,
    environment_name: str,
    pre_register_agents: bool,
    init_timeout_seconds: float,
) -> Tuple[List[AgentInitResult], List[str]]:
    if not specs:
        return [], []

    if len(specs) == 1:
        raw_results = await asyncio.gather(
            *[
                asyncio.create_task(
                    _initialize_agent_remote(
                        spec=spec,
                        agent_launcher_url=agent_launcher_url,
                        run_id=run_id,
                        environment_url=environment_url,
                        environment_name=environment_name,
                        pre_register_agents=pre_register_agents,
                        init_timeout_seconds=init_timeout_seconds,
                    )
                )
                for spec in specs
            ],
            return_exceptions=True,
        )
        successes: List[AgentInitResult] = []
        errors: List[str] = []
        for spec, raw_result in zip(specs, raw_results):
            if isinstance(raw_result, AgentInitResult):
                successes.append(raw_result)
                continue
            if isinstance(raw_result, HTTPException):
                detail = raw_result.detail if isinstance(raw_result.detail, str) else str(raw_result.detail)
                errors.append(f"{spec.agent_id}: {detail}")
                continue
            if isinstance(raw_result, Exception):
                errors.append(f"{spec.agent_id}: {raw_result}")
                continue
            errors.append(f"{spec.agent_id}: unexpected initialization result")
        return successes, errors

    batch_results, batch_errors = await init_agents_filesystem_batch(
        agent_launcher_url=agent_launcher_url,
        specs=specs,
        run_id=run_id,
        environment_url=environment_url,
        environment_name=environment_name,
        concurrency=concurrency,
        timeout_seconds=init_timeout_seconds,
    )
    errors = list(batch_errors)
    validated_specs: List[AgentInitSpec] = []
    for spec in specs:
        init_result = batch_results.get(spec.agent_id)
        if init_result is None:
            continue
        try:
            _validate_materialized_agent_result(
                spec=spec,
                init_result=init_result,
                environment_name=environment_name,
            )
            validated_specs.append(spec)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            errors.append(f"{spec.agent_id}: {detail}")

    if not pre_register_agents:
        return [AgentInitResult(spec=spec) for spec in validated_specs], errors

    if len(validated_specs) > 1:
        registration_results, registration_errors = await register_agents_with_environment_batch(
            environment_url=environment_url,
            specs=validated_specs,
            environment_name=environment_name,
            timeout_seconds=init_timeout_seconds,
        )
        errors.extend(registration_errors)
        successes: List[AgentInitResult] = []
        for spec in validated_specs:
            register_result = registration_results.get(spec.agent_id)
            if register_result is None:
                continue
            api_token = str(register_result.get("api_token") or "").strip() or None
            if not api_token:
                errors.append(f"{spec.agent_id}: missing api_token in registration result")
                continue
            successes.append(AgentInitResult(spec=spec, api_token=api_token))
        return successes, errors

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _register_runner(spec: AgentInitSpec) -> AgentInitResult:
        async with semaphore:
            register_result = await register_agent_with_environment(
                environment_url=environment_url,
                agent_id=spec.agent_id,
                environment_name=environment_name,
                agent_name=spec.agent_name,
                timeout_seconds=init_timeout_seconds,
            )
            api_token = str(register_result.get("api_token") or "").strip() or None
            if not api_token:
                raise HTTPException(
                    status_code=502,
                    detail=f"Environment registration missing api_token for {spec.agent_id}",
                )
            return AgentInitResult(spec=spec, api_token=api_token)

    registration_results = await asyncio.gather(
        *[asyncio.create_task(_register_runner(spec)) for spec in validated_specs],
        return_exceptions=True,
    )

    successes: List[AgentInitResult] = []
    for spec, raw_result in zip(validated_specs, registration_results):
        if isinstance(raw_result, AgentInitResult):
            successes.append(raw_result)
            continue
        if isinstance(raw_result, HTTPException):
            detail = raw_result.detail if isinstance(raw_result.detail, str) else str(raw_result.detail)
            errors.append(f"{spec.agent_id}: {detail}")
            continue
        if isinstance(raw_result, Exception):
            errors.append(f"{spec.agent_id}: {raw_result}")
            continue
        errors.append(f"{spec.agent_id}: unexpected registration result")
    return successes, errors


async def start_scheduler(
    agent_launcher_url: str,
    environment_url: str,
    environment_name: str,
    agent_tokens: Optional[Dict[str, str]] = None,
    agent_models: Optional[Dict[str, str]] = None,
    allowed_environment_urls: Optional[List[str]] = None,
    frequency_mode: str = "fixed",
    heartbeat_interval: str = "5s",
    heartbeat_interval_min: Optional[str] = None,
    heartbeat_interval_max: Optional[str] = None,
    random_seed: Optional[int] = None,
    heartbeat_timeout: str = "120s",
    retry_count: int = 3,
    retry_delay: str = "5s",
    jitter: str = "0s",
    max_parallel_agents: int = 10,
    max_heartbeats_per_agent: Optional[int] = None,
    model: str = "openai/gpt-5-mini",
) -> None:
    url = f"{agent_launcher_url}/scheduler/start"
    payload = {
        "config": {
            "environment_url": environment_url,
            "environment_name": environment_name,
            "agent_tokens": agent_tokens,
            "agent_models": agent_models,
            "allowed_environment_urls": allowed_environment_urls,
            "frequency_mode": frequency_mode,
            "heartbeat_interval": heartbeat_interval,
            "heartbeat_timeout": heartbeat_timeout,
            "retry_count": retry_count,
            "retry_delay": retry_delay,
            "jitter": jitter,
            "max_parallel_agents": max_parallel_agents,
            "max_heartbeats_per_agent": max_heartbeats_per_agent,
            "model": model,
        }
    }
    if heartbeat_interval_min:
        payload["config"]["heartbeat_interval_min"] = heartbeat_interval_min
    if heartbeat_interval_max:
        payload["config"]["heartbeat_interval_max"] = heartbeat_interval_max
    if random_seed is not None:
        payload["config"]["random_seed"] = int(random_seed)
    if not payload["config"].get("allowed_environment_urls"):
        payload["config"].pop("allowed_environment_urls", None)
    if not payload["config"].get("agent_tokens"):
        payload["config"].pop("agent_tokens", None)
    if not payload["config"].get("agent_models"):
        payload["config"].pop("agent_models", None)
    if not payload["config"].get("max_heartbeats_per_agent"):
        payload["config"].pop("max_heartbeats_per_agent", None)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise HTTPException(
                status_code=502,
                detail=f"Scheduler start failed for {agent_launcher_url}: {response.status_code} {detail}",
            )
