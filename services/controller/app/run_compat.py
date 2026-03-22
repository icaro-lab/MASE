"""Compatibility boundary for active run control behavior."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import logging
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.contract_validator import contract_validator
from app.database import Run as RunDB
from app.database import RunStatus as RunStatusEnum
from app.models import RunCreate as LegacyRunCreate
from app.heartbeat_contract import resolve_scheduler_heartbeat_timeout
from app.run_bootstrap import (
    AgentInitSpec,
    init_environment_run_context,
    load_environment_population_materializations,
    normalize_environment_config_for_run,
    persist_run_assignments,
    resolve_runtime_heartbeat_interval,
    run_bounded_agent_initialization,
    start_scheduler,
)
from app.run_config import (
    compute_resolved_bundle_hash,
    coerce_int,
    resolve_run_max_heartbeats_per_agent,
    resolve_run_max_ticks,
    resolve_run_runtime_limit,
)
from app.run_launcher import RunLaunchConfig, run_launcher
from app import run_public_ops
from app.run_read_model import build_run_response
from app.run_task_enforcement import (
    cancel_all_run_max_agent_heartbeat_tasks,
    cancel_all_run_max_tick_tasks,
    cancel_all_run_runtime_limit_tasks,
    cancel_run_max_agent_heartbeat_task,
    cancel_run_max_tick_task,
    cancel_run_runtime_limit_task,
    emit_run_terminal_event,
    recover_run_max_agent_heartbeat_tasks_on_startup,
    recover_run_max_tick_tasks_on_startup,
    recover_run_runtime_limit_tasks_on_startup,
    schedule_run_max_agent_heartbeat_task,
    schedule_run_max_tick_task,
    schedule_run_runtime_limit_task,
)
from app import run_binding


logger = logging.getLogger(__name__)


async def create_bound_run(
    *,
    environment_ref: str,
    environment_config: dict[str, Any],
    snapshot: dict[str, Any],
    request: LegacyRunCreate,
    db: Session,
) -> Any:
    provider = str(settings.agent_launcher_llm_provider or "").strip().lower()
    effective_api_key = request.api_key or settings.openrouter_api_key
    if not effective_api_key and provider != "dummy":
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing OpenRouter API key for run launch. Provide `api_key` in the request "
                "or configure `OPENROUTER_API_KEY` / `SIM_CTRL_OPENROUTER_API_KEY`."
            ),
        )

    normalized_env_config, _preview_metadata, _compile_source, _compiled_snapshot_hash = normalize_environment_config_for_run(
        environment_ref=environment_ref,
        environment_config=environment_config,
        db=db,
    )
    environment_id = (
        run_binding.normalize_environment_ref(environment_ref)
        or str(((normalized_env_config.get("launch") or {}).get("environment_id")) or "").strip()
        or str(normalized_env_config.get("environment_id") or "").strip()
    )
    resolved_bundle_hash = compute_resolved_bundle_hash(
        environment_ref=environment_ref,
        environment_config=normalized_env_config,
    )

    db_run = RunDB(
        resolved_bundle_hash=resolved_bundle_hash,
        seed=request.seed,
        status=RunStatusEnum.PENDING.value,
        started_at=datetime.utcnow(),
    )
    db.add(db_run)
    db.flush()
    run_binding.upsert_run_binding(
        db,
        run=db_run,
        environment_id=str(normalized_env_config.get("environment_id") or "").strip() or environment_id,
        runtime_id=str(normalized_env_config.get("runtime_id") or "").strip() or None,
        environment_ref=environment_ref,
        environment_config=copy.deepcopy(normalized_env_config),
        snapshot=copy.deepcopy(snapshot),
    )
    db.commit()
    db.refresh(db_run)

    try:
        assignment_snapshot = persist_run_assignments(
            db,
            run=db_run,
            environment_ref=environment_ref,
            environment_config=normalized_env_config,
        )
    except ValueError as assignment_error:
        db.rollback()
        db_run.status = RunStatusEnum.FAILED.value
        db_run.ended_at = datetime.utcnow()
        emit_run_terminal_event(
            db=db,
            run=db_run,
            terminal_status=RunStatusEnum.FAILED.value,
            terminal_reason=f"Run assignment resolution failed: {assignment_error}",
            terminal_source="create_run_assignment_resolution",
        )
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=f"Run assignment resolution failed: {assignment_error}",
        ) from assignment_error
    except Exception as assignment_error:
        db.rollback()
        db_run.status = RunStatusEnum.FAILED.value
        db_run.ended_at = datetime.utcnow()
        emit_run_terminal_event(
            db=db,
            run=db_run,
            terminal_status=RunStatusEnum.FAILED.value,
            terminal_reason=f"Failed to persist run assignments: {assignment_error}",
            terminal_source="create_run_assignment_persistence",
        )
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to persist run assignments: {assignment_error}",
        ) from assignment_error
    db.commit()
    db.refresh(db_run)

    launch_config = RunLaunchConfig(
        run_id=db_run.run_id,
        environment_id=str(normalized_env_config.get("environment_id") or "").strip() or environment_id,
        seed=request.seed,
        resolved_bundle_hash=resolved_bundle_hash,
        api_key=effective_api_key,
    )

    async def mark_run_failed_and_stop(
        status_code: int,
        detail: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                run_launcher.stop_run,
                db_run.run_id,
            )
        except Exception as stop_error:
            logger.warning(
                "create_run failure_handler_stop_failed run_id=%s error=%s",
                db_run.run_id,
                stop_error,
            )
        cancel_run_runtime_limit_task(db_run.run_id)
        cancel_run_max_tick_task(db_run.run_id)
        cancel_run_max_agent_heartbeat_task(db_run.run_id)

        db_run.status = RunStatusEnum.FAILED.value
        db_run.ended_at = datetime.utcnow()
        emit_run_terminal_event(
            db=db,
            run=db_run,
            terminal_status=RunStatusEnum.FAILED.value,
            terminal_reason=detail,
            terminal_source="create_run_failure",
        )
        db.commit()
        raise HTTPException(status_code=status_code, detail=detail)

    async def abort_if_run_cancelled(stage: str, *, stop_runtime: bool = False) -> None:
        db.refresh(db_run)
        if db_run.status != RunStatusEnum.CANCELLED.value:
            return
        if stop_runtime:
            try:
                await asyncio.to_thread(
                    run_launcher.stop_run,
                    db_run.run_id,
                )
            except Exception as stop_error:
                logger.warning(
                    "create_run cancel_handler_stop_failed run_id=%s stage=%s error=%s",
                    db_run.run_id,
                    stage,
                    stop_error,
                )
        cancel_run_runtime_limit_task(db_run.run_id)
        cancel_run_max_tick_task(db_run.run_id)
        cancel_run_max_agent_heartbeat_task(db_run.run_id)
        raise HTTPException(
            status_code=409,
            detail=f"Run {db_run.run_id} was cancelled during startup ({stage})",
        )

    launch_result = run_launcher.launch_run(launch_config)
    if launch_result.get("status") != "launched":
        launch_error = launch_result.get("error", "Unknown error")
        db_run.status = RunStatusEnum.FAILED.value
        db_run.ended_at = datetime.utcnow()
        emit_run_terminal_event(
            db=db,
            run=db_run,
            terminal_status=RunStatusEnum.FAILED.value,
            terminal_reason=f"Failed to launch run: {launch_error}",
            terminal_source="create_run_launch",
        )
        db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to launch run: {launch_error}")

    await abort_if_run_cancelled("launch", stop_runtime=True)

    health_result = await run_launcher.wait_for_run_services(
        run_id=db_run.run_id,
        environment_id=environment_id,
        timeout=settings.health_check_timeout,
        interval=settings.health_check_interval,
    )
    if health_result.get("status") != "healthy":
        error_msg = f"Per-run services failed health check: {health_result.get('error', 'Unknown error')}"
        await mark_run_failed_and_stop(status_code=503, detail=error_msg)

    await abort_if_run_cancelled("health_check", stop_runtime=True)

    service_urls = (
        health_result.get("service_urls")
        if isinstance(health_result.get("service_urls"), dict)
        else None
    )
    if not service_urls and isinstance(launch_result.get("service_urls"), dict):
        service_urls = launch_result.get("service_urls")
    if not service_urls:
        try:
            service_urls = run_launcher.get_service_urls(
                run_id=db_run.run_id,
                environment_id=environment_id,
            )
        except Exception as exc:
            await mark_run_failed_and_stop(
                status_code=500,
                detail=f"Failed to resolve runtime service URLs: {exc}",
            )

    environment_url = str((service_urls or {}).get("environment") or "").strip()
    if not environment_url:
        await mark_run_failed_and_stop(
            status_code=500,
            detail="Run runtime context missing environment service URL",
        )

    validation_result = await contract_validator.validate_environment(environment_url, strict=False)
    if not validation_result.is_compatible():
        error_msg = f"Environment contract validation failed for {environment_url}: "
        error_msg += "; ".join(validation_result.errors)
        if validation_result.missing_required_endpoints:
            error_msg += f". Missing required endpoints: {', '.join(validation_result.missing_required_endpoints)}"
        await mark_run_failed_and_stop(status_code=400, detail=error_msg)

    bootstrap_result = await init_environment_run_context(
        environment_url=environment_url,
        run_id=db_run.run_id,
        environment_id=environment_id,
        environment_params=(
            normalized_env_config.get("environment_params")
            if isinstance(normalized_env_config.get("environment_params"), dict)
            else {}
        ),
        assignment_snapshot=assignment_snapshot,
    )
    if not bootstrap_result.get("success"):
        bootstrap_error = str(bootstrap_result.get("error") or "unknown_error")
        logger.warning(
            "create_run run_init_compatibility_fallback run_id=%s status_code=%s error=%s",
            db_run.run_id,
            bootstrap_result.get("status_code"),
            bootstrap_error,
        )

    await abort_if_run_cancelled("environment_bootstrap", stop_runtime=True)

    resolved_runtime_limit_minutes, _runtime_limit_source = resolve_run_runtime_limit(normalized_env_config)
    agent_count = coerce_int(assignment_snapshot.get("agent_count"), default=0)
    default_runtime_id = str(assignment_snapshot.get("runtime_id") or "openclaw")
    default_runtime_content_hash = (
        str(assignment_snapshot.get("runtime_content_hash") or "").strip() or None
    )
    default_agent_model = (
        str(assignment_snapshot.get("agent_model") or normalized_env_config.get("agent_model") or "openai/gpt-5-mini")
        .strip()
        or "openai/gpt-5-mini"
    )
    assignment_map_raw = assignment_snapshot.get("assignment_map")
    assignment_map = assignment_map_raw if isinstance(assignment_map_raw, dict) else {}

    initialized_agents: list[str] = []
    agent_tokens: dict[str, str] = {}
    agent_models: dict[str, str] = {}
    display_names_cfg = normalized_env_config.get("agent_display_names")
    if not isinstance(display_names_cfg, dict):
        display_names_cfg = {}
    try:
        environment_population_materializations = load_environment_population_materializations(normalized_env_config)
    except ValueError as exc:
        await mark_run_failed_and_stop(status_code=400, detail=str(exc))
    pre_register_agents = bool(normalized_env_config.get("pre_register_agents"))

    if agent_count > 0:
        agent_launcher_url = str((service_urls or {}).get("agent_worker") or "").strip()
        if not agent_launcher_url:
            await mark_run_failed_and_stop(
                status_code=500,
                detail="Run runtime context missing agent_worker service URL",
            )
        environment_name = environment_id

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for attempt in range(30):
                    try:
                        launcher_health = await client.get(f"{agent_launcher_url}/health")
                        launcher_health.raise_for_status()
                        break
                    except Exception as exc:
                        if attempt == 29:
                            raise exc
                        await asyncio.sleep(1)
        except Exception as exc:
            await mark_run_failed_and_stop(
                status_code=503,
                detail=(
                    f"Agent launcher URL is not reachable before scheduler start "
                    f"({agent_launcher_url}): {exc}"
                ),
            )

        agent_specs: list[Any] = []
        for i in range(agent_count):
            agent_id = f"agent-{i+1}"
            agent_assignment = assignment_map.get(agent_id)
            if not isinstance(agent_assignment, dict):
                agent_assignment = {}

            assigned_runtime_id = (
                str(agent_assignment.get("runtime_id") or default_runtime_id).strip()
                or default_runtime_id
            )
            assigned_runtime_content_hash = (
                str(agent_assignment.get("content_hash") or default_runtime_content_hash or "").strip()
                or None
            )
            assigned_model = (
                str(agent_assignment.get("model_id") or default_agent_model).strip()
                or default_agent_model
            )
            assigned_population_group = str(agent_assignment.get("population_group") or "").strip() or None
            assigned_role_label = (
                str(agent_assignment.get("role_label") or agent_assignment.get("role") or "").strip()
                or None
            )
            agent_name = str(display_names_cfg.get(agent_id) or f"{assigned_runtime_id}-{i+1}")
            population_materialization = (
                environment_population_materializations.get(assigned_population_group or "")
                if assigned_population_group
                else {}
            )
            if not population_materialization and len(environment_population_materializations) == 1:
                population_materialization = next(iter(environment_population_materializations.values()))

            agent_specs.append(
                AgentInitSpec(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    runtime_id=assigned_runtime_id,
                    runtime_content_hash=assigned_runtime_content_hash,
                    model_id=assigned_model,
                    population_group=assigned_population_group,
                    role_label=assigned_role_label,
                    agents=population_materialization.get("agents"),
                    identity=population_materialization.get("identity"),
                    soul=population_materialization.get("soul"),
                    tools=population_materialization.get("tools"),
                    bootstrap=population_materialization.get("bootstrap"),
                    user=population_materialization.get("user"),
                    heartbeat=population_materialization.get("heartbeat"),
                )
            )

        default_agent_init_concurrency = min(
            max(1, coerce_int(normalized_env_config.get("max_parallel_agents"), default=50)),
            100,
        )
        resolved_agent_init_concurrency = max(
            1,
            min(
                coerce_int(
                    normalized_env_config.get("agent_init_concurrency"),
                    default=default_agent_init_concurrency,
                ),
                200,
            ),
        )
        resolved_agent_init_timeout_seconds = max(
            30,
            coerce_int(normalized_env_config.get("agent_init_timeout_seconds"), default=600),
        )
        init_results, initialization_errors = await run_bounded_agent_initialization(
            specs=agent_specs,
            concurrency=resolved_agent_init_concurrency,
            agent_launcher_url=agent_launcher_url,
            run_id=db_run.run_id,
            environment_url=environment_url,
            environment_name=environment_name,
            pre_register_agents=pre_register_agents,
            init_timeout_seconds=float(resolved_agent_init_timeout_seconds),
        )

        await abort_if_run_cancelled("agent_initialization", stop_runtime=True)

        if initialization_errors:
            preview_errors = initialization_errors[:20]
            if len(initialization_errors) > len(preview_errors):
                preview_errors.append(f"... and {len(initialization_errors) - len(preview_errors)} more")
            await mark_run_failed_and_stop(
                status_code=500,
                detail=("Agent initialization failed before scheduler start: " + "; ".join(preview_errors)),
            )

        for result in init_results:
            spec = result.spec
            agent_models[spec.agent_id] = spec.model_id
            initialized_agents.append(spec.agent_id)
            if result.api_token:
                agent_tokens[spec.agent_id] = result.api_token

        if not initialized_agents:
            await mark_run_failed_and_stop(
                status_code=500,
                detail="No agents initialized successfully; refusing to start scheduler.",
            )

        try:
            scheduler_default_model = default_agent_model
            if not scheduler_default_model and agent_models:
                scheduler_default_model = sorted(set(agent_models.values()))[0]
            scheduler_agent_models = {
                agent_id: model_id
                for agent_id, model_id in agent_models.items()
                if str(model_id or "").strip()
            }
            heartbeat_config = normalized_env_config.get("heartbeat", {})
            if not isinstance(heartbeat_config, dict):
                heartbeat_config = {}
            resolved_max_heartbeats_per_agent, _ = resolve_run_max_heartbeats_per_agent(normalized_env_config)
            interval_from_env = str(heartbeat_config.get("interval") or "").strip() or None
            interval_from_runtime = resolve_runtime_heartbeat_interval(
                runtime_id=default_runtime_id,
            )
            resolved_heartbeat_interval = interval_from_env or interval_from_runtime or "5s"
            resolved_frequency_mode = str(heartbeat_config.get("frequency_mode") or "fixed").strip().lower() or "fixed"
            resolved_interval_min = str(heartbeat_config.get("interval_min") or "").strip() or None
            resolved_interval_max = str(heartbeat_config.get("interval_max") or "").strip() or None
            raw_allowed_environment_urls = heartbeat_config.get("allowed_environment_urls")
            resolved_allowed_environment_urls: list[str] = []
            if isinstance(raw_allowed_environment_urls, list):
                for item in raw_allowed_environment_urls:
                    value = str(item or "").strip()
                    if value and value != environment_url and value not in resolved_allowed_environment_urls:
                        resolved_allowed_environment_urls.append(value)
            resolved_random_seed = coerce_int(heartbeat_config.get("random_seed"), default=None)
            resolved_heartbeat_timeout = resolve_scheduler_heartbeat_timeout(heartbeat_config)
            await start_scheduler(
                agent_launcher_url=agent_launcher_url,
                environment_url=environment_url,
                environment_name=environment_name,
                agent_tokens=agent_tokens or None,
                agent_models=scheduler_agent_models or None,
                allowed_environment_urls=resolved_allowed_environment_urls or None,
                frequency_mode=resolved_frequency_mode,
                heartbeat_interval=resolved_heartbeat_interval,
                heartbeat_interval_min=resolved_interval_min,
                heartbeat_interval_max=resolved_interval_max,
                random_seed=resolved_random_seed,
                heartbeat_timeout=resolved_heartbeat_timeout,
                retry_count=int(heartbeat_config.get("retry_count", 3)),
                retry_delay=heartbeat_config.get("retry_delay", "5s"),
                jitter=heartbeat_config.get("jitter", "0s"),
                max_parallel_agents=normalized_env_config.get("max_parallel_agents", min(10, agent_count)),
                max_heartbeats_per_agent=resolved_max_heartbeats_per_agent,
                model=scheduler_default_model,
            )
            await abort_if_run_cancelled("scheduler_start", stop_runtime=True)
        except HTTPException as exc:
            detail = str(getattr(exc, "detail", "") or exc)
            await mark_run_failed_and_stop(
                status_code=503,
                detail=f"Failed to start heartbeat scheduler for run {db_run.run_id}: {detail}",
            )
        except Exception as exc:
            await mark_run_failed_and_stop(
                status_code=503,
                detail=f"Failed to start heartbeat scheduler for run {db_run.run_id}: {exc}",
            )

    db_run.status = RunStatusEnum.RUNNING.value
    db.commit()
    db.refresh(db_run)
    if resolved_runtime_limit_minutes is not None:
        schedule_run_runtime_limit_task(
            run_id=db_run.run_id,
            runtime_limit_minutes=resolved_runtime_limit_minutes,
            enforcement_source="run_start",
        )
    resolved_max_ticks, _ = resolve_run_max_ticks(normalized_env_config)
    if resolved_max_ticks is not None:
        schedule_run_max_tick_task(
            run_id=db_run.run_id,
            max_ticks=resolved_max_ticks,
            enforcement_source="run_start",
        )
    resolved_max_heartbeats_per_agent, _ = resolve_run_max_heartbeats_per_agent(normalized_env_config)
    if resolved_max_heartbeats_per_agent is not None:
        schedule_run_max_agent_heartbeat_task(
            run_id=db_run.run_id,
            max_heartbeats_per_agent=resolved_max_heartbeats_per_agent,
            enforcement_source="run_start",
            target_agents=agent_count,
        )

    return build_run_response(
        db_run,
        db,
        agent_count=agent_count,
        initialized_agents=initialized_agents if initialized_agents else None,
    )


async def create_run(version_id: str, request: LegacyRunCreate, *, db: Session) -> Any:
    raise HTTPException(
        status_code=410,
        detail="Version-based run creation is removed from the runtime/environment/run controller",
    )


async def stop_run(run_id: str, *, db: Session) -> Any:
    return await run_public_ops.stop_run(run_id, db=db)


async def pause_run(run_id: str, *, db: Session) -> Any:
    return await run_public_ops.pause_run(run_id, db=db)


async def resume_run(run_id: str, *, db: Session) -> Any:
    return await run_public_ops.resume_run(run_id, db=db)


async def restart_run_stack(run_id: str, *, db: Session) -> Any:
    return await run_public_ops.restart_run_stack(run_id, db=db)


async def delete_run(run_id: str, *, db: Session) -> Any:
    return await run_public_ops.delete_run(run_id, db=db)


async def get_run_cost(run_id: str, *, db: Session) -> dict[str, Any]:
    return await run_public_ops.get_run_cost(run_id, db=db)


async def get_run_scheduler_status(
    run_id: str,
    *,
    include_agent_states: bool = False,
    db: Session,
) -> dict[str, Any]:
    return await run_public_ops.get_run_scheduler_status(
        run_id,
        include_agent_states=include_agent_states,
        db=db,
    )


async def get_run_scheduler_progress(run_id: str, *, db: Session) -> dict[str, Any]:
    return await run_public_ops.get_run_scheduler_progress(run_id, db=db)
