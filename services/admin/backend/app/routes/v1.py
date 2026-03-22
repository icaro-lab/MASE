"""Canonical admin API routes for runtimes, environments, and runs."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import Settings, get_settings
from ..orchestrator_client import ControllerProxyError, ControllerClient
from ..settings_store import get_openrouter_fallback_key


router = APIRouter(prefix="/v1", tags=["v1"])


def get_controller(settings: Settings = Depends(get_settings)) -> ControllerClient:
    return ControllerClient(settings)


def _raise_proxy_error(exc: ControllerProxyError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/runtimes")
async def list_runtimes(controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.list_runtimes()
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runtimes/{runtime_id}")
async def get_runtime(runtime_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.get_runtime(runtime_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/environments")
async def list_environments(controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.list_environments()
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/environments/{environment_id}")
async def get_environment(
    environment_id: str,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.get_environment(environment_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/environments/{environment_id}/validate")
async def validate_environment(
    environment_id: str,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.validate_environment(environment_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/runs")
async def create_run(
    request: dict[str, Any],
    controller: ControllerClient = Depends(get_controller),
):
    fallback_api_key = get_openrouter_fallback_key()
    requested_api_key = str(request.get("api_key") or "").strip()
    resolved_api_key = requested_api_key or fallback_api_key
    payload = dict(request)
    if resolved_api_key:
        payload["api_key"] = resolved_api_key

    try:
        return await controller.create_run(payload)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runs")
async def list_runs(
    environment_id: Optional[str] = None,
    status: Optional[str] = None,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.list_runs(environment_id=environment_id, status=status)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runs/{run_id}")
async def get_run(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.get_run(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runs/{run_id}/snapshot")
async def get_run_snapshot(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.get_run_snapshot(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runs/{run_id}/cost")
async def get_run_cost(
    run_id: str,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.get_run_cost(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/runs/{run_id}/scheduler/status")
async def get_run_scheduler_status(
    run_id: str,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.get_run_scheduler_status(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.stop_run(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/runs/{run_id}/restart-stack")
async def restart_run_stack(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.restart_run_stack(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/runs/{run_id}/pause")
async def pause_run(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.pause_run(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.resume_run(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, controller: ControllerClient = Depends(get_controller)):
    try:
        return await controller.delete_run(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/events/runs/{run_id}")
async def get_run_events(
    run_id: str,
    event_type: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.get_run_events(
            run_id=run_id,
            event_type=event_type,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.get("/events/runs/{run_id}/count")
async def get_run_event_count(
    run_id: str,
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.get_run_event_count(run_id)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()


@router.post("/events/export")
async def export_events(
    request: dict[str, Any],
    controller: ControllerClient = Depends(get_controller),
):
    try:
        return await controller.export_events(request)
    except ControllerProxyError as exc:
        _raise_proxy_error(exc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        await controller.close()
